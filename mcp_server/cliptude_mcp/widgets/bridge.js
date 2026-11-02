/* Cliptude MCP App bridge: JSON-RPC 2.0 over postMessage with the host (MCP Apps, SEP-1865).
 * Spec: https://modelcontextprotocol.io/specification/draft/extensions/apps
 * View -> Host requests: ui/initialize, ui/message, ui/update-model-context, ui/open-link, tools/call
 * View -> Host notifications: ui/notifications/initialized, ui/notifications/size-changed
 * Host -> View notifications: ui/notifications/tool-input, ui/notifications/tool-result,
 *   ui/notifications/tool-cancelled, ui/notifications/host-context-changed
 * Host -> View requests: ui/resource-teardown, ping
 * ChatGPT speaks this same bridge (Apps SDK "Build a ChatGPT UI"); when a host does not answer ui/initialize but
 * offers window.openai (the Apps SDK's own API), the widget uses that instead: toolInput/toolOutput, callTool,
 * sendFollowUpMessage, openExternal and the openai:set_globals event.
 * Inlined into every widget by cliptude_mcp/apps.py, so the served HTML has no external script. */
var Cliptude = (function () {
  "use strict";
  var PROTOCOL_VERSION = "2026-01-26";
  var nextId = 1;
  var pending = {};
  var handlers = { input: null, result: null, cancelled: null, context: null };
  var state = { hostContext: {}, hostCapabilities: {}, toolInput: null, connected: false, openai: false };

  function openaiApi() {
    try { return window.openai && typeof window.openai === "object" ? window.openai : null; } catch (e) { return null; }
  }

  function post(message) {
    if (window.parent && window.parent !== window) window.parent.postMessage(message, "*");
  }

  function request(method, params, timeoutMs) {
    var id = nextId++;
    return new Promise(function (resolve, reject) {
      pending[id] = { resolve: resolve, reject: reject };
      post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
      setTimeout(function () {
        if (pending[id]) {
          delete pending[id];
          reject(new Error(method + " timed out"));
        }
      }, timeoutMs || 60000);
    });
  }

  function notify(method, params) {
    post({ jsonrpc: "2.0", method: method, params: params || {} });
  }

  function respond(id, result) {
    post({ jsonrpc: "2.0", id: id, result: result || {} });
  }

  function applyContext(ctx) {
    if (!ctx) return;
    var root = document.documentElement;
    if (ctx.theme === "dark" || ctx.theme === "light") {
      root.setAttribute("data-theme", ctx.theme);
      root.style.colorScheme = ctx.theme;
    }
    var styles = ctx.styles || {};
    var vars = styles.variables || {};
    Object.keys(vars).forEach(function (key) {
      if (vars[key] != null && key.indexOf("--") === 0) root.style.setProperty(key, vars[key]);
    });
    if (styles.css && styles.css.fonts && !document.getElementById("host-fonts")) {
      var tag = document.createElement("style");
      tag.id = "host-fonts";
      tag.textContent = styles.css.fonts;
      document.head.appendChild(tag);
    }
    var dims = ctx.containerDimensions || {};
    if (dims.maxHeight) root.style.maxHeight = dims.maxHeight + "px";
    if (handlers.context) handlers.context(state.hostContext);
  }

  function merge(target, source) {
    Object.keys(source || {}).forEach(function (key) { target[key] = source[key]; });
    return target;
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent) return;
    var msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === undefined && msg.id !== undefined && msg.id !== null) {
      var waiter = pending[msg.id];
      if (!waiter) return;
      delete pending[msg.id];
      if (msg.error) waiter.reject(new Error(msg.error.message || "Host error"));
      else waiter.resolve(msg.result || {});
      return;
    }
    var params = msg.params || {};
    if (msg.id !== undefined && msg.id !== null) {
      // Requests from the host.
      if (msg.method === "ui/resource-teardown" || msg.method === "ping") {
        Media.stop();
        respond(msg.id, {});
      } else {
        post({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: "Method not found" } });
      }
      return;
    }
    switch (msg.method) {
      case "ui/notifications/tool-input":
        state.toolInput = params.arguments || {};
        if (handlers.input) handlers.input(state.toolInput);
        break;
      case "ui/notifications/tool-result":
        if (handlers.result) handlers.result(resultData(params), params);
        break;
      case "ui/notifications/tool-cancelled":
        if (handlers.cancelled) handlers.cancelled(params.reason || "");
        break;
      case "ui/notifications/host-context-changed":
        merge(state.hostContext, params);
        applyContext(params);
        break;
    }
  });

  /** The tool's data: structuredContent, else the JSON in its first text block. */
  function resultData(result) {
    if (!result) return null;
    if (result.structuredContent && typeof result.structuredContent === "object") return result.structuredContent;
    var blocks = result.content || [];
    for (var i = 0; i < blocks.length; i++) {
      if (blocks[i] && blocks[i].type === "text") {
        try { return JSON.parse(blocks[i].text); } catch (e) { return { error: blocks[i].text }; }
      }
    }
    return null;
  }

  var lastSize = "";
  function reportSize() {
    var el = document.documentElement;
    var width = Math.ceil(el.scrollWidth);
    var height = Math.ceil(document.body ? document.body.scrollHeight : el.scrollHeight);
    var key = width + "x" + height;
    if (key === lastSize) return;
    lastSize = key;
    if (state.openai) {
      // Apps SDK: notifyIntrinsicHeight (argument shape not documented; ChatGPT also sizes the frame itself).
      try { if (window.openai.notifyIntrinsicHeight) window.openai.notifyIntrinsicHeight(height); } catch (e) {}
      return;
    }
    notify("ui/notifications/size-changed", { width: width, height: height });
  }

  /** ChatGPT's window.openai: the tool's arguments and result are globals, updated by openai:set_globals. */
  var lastOutput = null;
  function connectOpenai() {
    var api = openaiApi();
    state.openai = true;
    state.connected = true;
    state.hostCapabilities = { serverTools: typeof api.callTool === "function" ? {} : undefined };
    if (!state.hostCapabilities.serverTools) delete state.hostCapabilities.serverTools;
    state.hostContext = { theme: api.theme, displayMode: api.displayMode,
      containerDimensions: api.maxHeight ? { maxHeight: api.maxHeight } : {} };
    applyContext(state.hostContext);
    function deliver() {
      var input = api.toolInput || null;
      if (input && input !== state.toolInput) {
        state.toolInput = input;
        if (handlers.input) handlers.input(input);
      }
      var output = api.toolOutput;
      if (output && output !== lastOutput) {
        lastOutput = output;
        if (handlers.result) handlers.result(output && output.structuredContent ? resultData(output) : output, {});
      }
    }
    window.addEventListener("openai:set_globals", function (event) {
      var globals = (event && event.detail && event.detail.globals) || {};
      if (globals.theme) applyContext({ theme: globals.theme });
      deliver();
    });
    deliver();
    if (window.ResizeObserver) new ResizeObserver(reportSize).observe(document.body);
    reportSize();
    return true;
  }

  function connect(options) {
    handlers.input = options.onInput || null;
    handlers.result = options.onResult || null;
    handlers.cancelled = options.onCancelled || null;
    handlers.context = options.onContext || null;
    if (!window.parent || window.parent === window) {
      return Promise.resolve(openaiApi() ? connectOpenai() : false);
    }
    // A host with window.openai answers quickly or not at all; do not keep its user waiting a minute.
    return request("ui/initialize", {
      appInfo: { name: options.name, version: "1.0.0" },
      appCapabilities: { availableDisplayModes: ["inline"] },
      protocolVersion: PROTOCOL_VERSION
    }, openaiApi() ? 4000 : 60000).catch(function (error) {
      if (openaiApi()) return null;
      throw error;
    }).then(function (result) {
      if (result === null) return connectOpenai();
      state.hostContext = result.hostContext || {};
      state.hostCapabilities = result.hostCapabilities || {};
      state.connected = true;
      applyContext(state.hostContext);
      notify("ui/notifications/initialized", {});
      if (window.ResizeObserver) new ResizeObserver(reportSize).observe(document.body);
      reportSize();
      return true;
    });
  }

  /** Post a user message into the conversation (ui/message); falls back to model context. */
  function sendMessage(text) {
    if (state.openai) {
      return Promise.resolve(window.openai.sendFollowUpMessage({ prompt: text }));
    }
    return request("ui/message", { role: "user", content: [{ type: "text", text: text }] }).then(function (res) {
      if (res && res.isError) throw new Error("The host did not accept the message");
      return res;
    });
  }

  function updateModelContext(text, structured) {
    if (state.openai) return Promise.reject(new Error("Not supported here"));
    var params = { content: [{ type: "text", text: text }] };
    if (structured) params.structuredContent = structured;
    return request("ui/update-model-context", params);
  }

  function openLink(url) {
    if (state.openai) return Promise.resolve(window.openai.openExternal({ href: url }));
    return request("ui/open-link", { url: url });
  }

  /** Call a server tool from the widget. Only ever from a user's click (see review.html). */
  function callTool(name, args) {
    if (state.openai) {
      return Promise.resolve(window.openai.callTool(name, args || {})).then(function (res) {
        return res && (res.structuredContent || res.content) ? resultData(res) : res;
      });
    }
    return request("tools/call", { name: name, arguments: args || {} }).then(resultData);
  }

  function canCallTools() {
    return !!state.hostCapabilities.serverTools;
  }

  return {
    connect: connect,
    sendMessage: sendMessage,
    updateModelContext: updateModelContext,
    openLink: openLink,
    callTool: callTool,
    canCallTools: canCallTools,
    reportSize: reportSize,
    state: state
  };
})();

/* One preview playing at a time across the widget (audio or video). */
var Media = (function () {
  "use strict";
  var current = null;
  var onChange = null;
  function stop() {
    if (current) {
      try { current.el.pause(); } catch (e) {}
      var was = current;
      current = null;
      if (onChange) onChange(was.key, false);
    }
  }
  function toggleAudio(key, url) {
    if (current && current.key === key) { stop(); return false; }
    stop();
    var audio = new Audio(url);
    audio.preload = "none";
    current = { key: key, el: audio };
    audio.addEventListener("ended", function () { if (current && current.el === audio) stop(); });
    audio.addEventListener("error", function () {
      if (current && current.el === audio) { stop(); UI.toast("That preview could not be played."); }
    });
    var played = audio.play();
    if (played && played.catch) played.catch(function () {
      if (current && current.el === audio) { stop(); UI.toast("That preview could not be played."); }
    });
    if (onChange) onChange(key, true);
    return true;
  }
  function claimVideo(key, video) {
    if (current && current.el !== video) stop();
    current = { key: key, el: video };
    if (onChange) onChange(key, true);
  }
  function release(video) {
    if (current && current.el === video) { current = null; }
  }
  return {
    stop: stop, toggleAudio: toggleAudio, claimVideo: claimVideo, release: release,
    onChange: function (fn) { onChange = fn; },
    playing: function () { return current ? current.key : null; }
  };
})();

/* Small DOM helpers shared by the widgets. */
var UI = (function () {
  "use strict";
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.indexOf("on") === 0 && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (key === "style" && typeof value === "object") Object.keys(value).forEach(function (k) { el.style[k] = value[k]; });
      else el.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  var toastTimer = null;
  function toast(text) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 3200);
  }
  /** Save a choice for the agent without writing in the user's message box.
   *  Claude shows "Use caution before running this prompt" on any message an app drafts (ui/message), and it
   *  does not hand ui/update-model-context to the model (a pick saved only that way was lost). So the pick is
   *  kept on Cliptude (save_choice), the widget asks the user to type "next", and the agent reads it with
   *  get_choice. ChatGPT has no such warning: there, and wherever saving fails, the pick is sent as a message. */
  function choose(text, structured) {
    if (Cliptude.state.openai || !Cliptude.canCallTools()) return sendAsMessage(text);
    return Cliptude.callTool("save_choice", { message: text, choice: structured || {} }).then(function (res) {
      if (!res || res.error || res.saved !== true) throw new Error("not saved");
      Cliptude.updateModelContext(text, structured).catch(function () {});
      saved(text);
      return true;
    }).catch(function () { return sendAsMessage(text); });
  }
  function sendAsMessage(text) {
    return Cliptude.sendMessage(text).then(function () {
      toast("Sent to the chat.");
      return true;
    }).catch(function () {
      toast("Copy this into the chat: " + text);
      return false;
    });
  }
  /** A note that stays until the next choice: what was saved, and how to go on. */
  function saved(text) {
    var note = document.getElementById("saved-note");
    if (!note) {
      note = h("div", { id: "saved-note", class: "saved-note", role: "status", "aria-live": "polite" });
      var app = document.querySelector(".app") || document.body;
      app.appendChild(note);
    }
    clear(note);
    note.appendChild(h("strong", {}, ["Saved. "]));
    note.appendChild(document.createTextNode("Type "));
    note.appendChild(h("kbd", {}, ["next"]));
    note.appendChild(document.createTextNode(" in the chat to continue."));
    note.appendChild(h("span", { class: "saved-what" }, [text]));
    try { note.scrollIntoView({ block: "nearest" }); } catch (e) {}
    if (Cliptude.reportSize) Cliptude.reportSize();
  }
  function status(el, text, kind) {
    clear(el).appendChild(h("div", { class: "status " + (kind || ""), role: kind === "error" ? "alert" : "status" }, [text]));
  }
  function initials(name) {
    var letters = String(name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
    return (letters.slice(0, 2).map(function (p) { return p[0]; }).join("") || "?").toUpperCase();
  }
  function img(src, alt, fallbackText) {
    var box = h("span", { class: "thumb" });
    if (src) {
      var image = h("img", { src: src, alt: alt || "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
      image.addEventListener("error", function () {
        box.replaceChild(h("span", { class: "thumb-fallback", "aria-hidden": "true" }, [fallbackText || ""]), image);
      }, { once: true });
      box.appendChild(image);
    } else {
      box.appendChild(h("span", { class: "thumb-fallback", "aria-hidden": "true" }, [fallbackText || ""]));
    }
    return box;
  }
  function errorText(data) {
    if (!data) return "Nothing came back from Cliptude.";
    if (typeof data.error === "string" && data.error) {
      return data.error.indexOf("<") >= 0 ? "Cliptude returned an error" + (data.status ? " (" + data.status + ")" : "") + "." : data.error;
    }
    return "";
  }
  return { h: h, clear: clear, toast: toast, choose: choose, status: status, initials: initials, img: img, errorText: errorText };
})();
