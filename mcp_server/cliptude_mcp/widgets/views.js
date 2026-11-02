/* Views shared by the job, review and publish-kit widgets (inlined by cliptude_mcp/apps.py after bridge.js).
 *
 *   Views.mount(container, data, ctx)  picks the view for one tool result:
 *     a 428 review          -> review(): settings, warnings, acknowledgements, credits, Confirm / Change something
 *     a script or narration -> script(): numbered narration, word count vs the duration's budget, Approve / Ask
 *     a job or product job  -> progress(): status, progress bar, step; the publish kit inline once it is ready
 *     anything else         -> result(): job / run / render / avatar id and status, or the error
 *   Views.publishKit(container, kit)   the publish kit (video, thumbnails, titles, description, chapters, tags)
 *
 * Safety: the only tools/call that spends anything is in confirmReview, which runs from the Confirm button's
 * click handler and checks event.isTrusted. Nothing here confirms a review on its own. */
var Views = (function () {
  "use strict";
  var h = UI.h;

  /* ------------------------------------------------------------------ helpers */
  function open(url) { Cliptude.openLink(url).catch(function () { UI.toast("The link could not be opened here."); }); }

  function selectText(el) {
    try {
      var range = document.createRange(); range.selectNodeContents(el);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    } catch (e) {}
  }

  /** Clipboard API (clipboardWrite permission), else execCommand, else select the text for the user. */
  function copy(text, el) {
    function fallback() {
      var area = h("textarea", { style: { position: "fixed", opacity: "0", top: "0", left: "0" } });
      area.value = text; document.body.appendChild(area); area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      if (ok) { UI.toast("Copied."); return; }
      if (el) selectText(el);
      UI.toast("Selected. Press ⌘C or Ctrl+C to copy.");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { UI.toast("Copied."); }, fallback);
    } else fallback();
  }

  function words(text) { return String(text || "").split(/\s+/).filter(Boolean).length; }
  function num(n) { return Number(n || 0).toLocaleString("en-US"); }
  function str(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "boolean") return v ? "On" : "Off";
    if (Array.isArray(v)) return v.join(", ");
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  function section(title, children, extra) {
    return h("section", { class: "sec" }, [
      h("div", { class: "sec-head" }, [h("h2", {}, [title]), extra || null])
    ].concat(children));
  }

  /* ------------------------------------------------------------------ script */
  // Spoken words for a duration bucket at ~150 words a minute (MCP instructions / cliptude-script skill).
  var WORD_BUDGET = {
    "6-8": [900, 1200], "8-10": [1250, 1540], "10-12": [1550, 1850], "18-20": [2800, 3090],
    "30-40": [4700, 6180], "50-60": [7800, 9270]
  };
  var MIN_WORDS = 880;

  function budgetLine(count, duration) {
    var range = WORD_BUDGET[String(duration || "")];
    var minutes = count / 150;
    var est = "≈ " + (minutes < 10 ? minutes.toFixed(1) : Math.round(minutes)) + " min spoken";
    if (!range) {
      return { text: num(count) + " words · " + est + (count < MIN_WORDS ? " · under the " + MIN_WORDS + "-word minimum" : ""),
               kind: count < MIN_WORDS ? "warn" : "" };
    }
    var kind = count < range[0] || count > range[1] ? "warn" : "ok";
    var verdict = count < range[0] ? "short for " : count > range[1] ? "long for " : "fits ";
    return { text: num(count) + " words · " + est + " · " + verdict + duration + " min (" + num(range[0]) + "–" + num(range[1]) + ")", kind: kind };
  }

  /** data: {script, duration, title} or a narration {sentences:[{id,text,start,end}], duration_s}. */
  function script(container, data, ctx) {
    var sentences = Array.isArray(data.sentences) ? data.sentences : null;
    var text = sentences ? sentences.map(function (s) { return s.text || ""; }).join(" ") : String(data.script || "");
    var count = words(text);
    var isNarration = !!sentences;
    var body = h("div", { class: "script", tabindex: "0" });
    if (isNarration) {
      sentences.forEach(function (s, i) {
        body.appendChild(h("p", { class: "sentence" }, [
          h("span", { class: "num", "aria-label": "Sentence " + (s.id !== undefined ? s.id : i) }, [String(s.id !== undefined ? s.id : i)]),
          h("span", {}, [s.text || ""])
        ]));
      });
    } else {
      text.split(/\n\s*\n/).map(function (p) { return p.trim(); }).filter(Boolean).forEach(function (p) {
        body.appendChild(h("p", {}, [p]));
      });
    }
    var facts = [];
    if (isNarration) {
      facts.push(h("span", { class: "chip" }, [num(count) + " words"]));
      facts.push(h("span", { class: "chip" }, [sentences.length + " sentences"]));
      if (data.duration_s) facts.push(h("span", { class: "chip" }, [Math.floor(data.duration_s / 60) + ":" + String(Math.round(data.duration_s % 60)).padStart(2, "0") + " voiced"]));
    } else {
      var b = budgetLine(count, data.duration);
      facts.push(h("span", { class: "chip " + (b.kind === "warn" ? "warn" : b.kind === "ok" ? "ok" : "") }, [b.text]));
    }
    var noun = isNarration ? "narration" : "script";
    var changes = h("div", { class: "changes", hidden: true }, [
      h("label", { class: "sr-only", for: "changes-text" }, ["What should change?"]),
      h("textarea", { id: "changes-text", rows: "3", placeholder: "What should change? (a fact, the opening, the length…)" }),
      h("div", { class: "toolbar" }, [h("button", { type: "button", class: "primary", onclick: function () {
        var asked = (document.getElementById("changes-text").value || "").trim();
        UI.choose(asked ? "Please change the " + noun + ": " + asked
          : "I want changes to the " + noun + ". Ask me what to change.", { approved: false });
      } }, ["Send"])])
    ]);
    UI.clear(container);
    container.appendChild(h("div", { class: "head" }, [
      h("h1", {}, [(isNarration ? "Narration" : "Script") + (data.title ? " · " + data.title : "")]),
      h("span", { class: "chips" }, facts)
    ]));
    if (data.warning) container.appendChild(h("div", { class: "callout warn", role: "note" }, [data.warning]));
    container.appendChild(body);
    container.appendChild(changes);
    container.appendChild(h("div", { class: "selection" }, [
      h("span", { class: "toolbar" }, [
        h("button", { type: "button", onclick: function () { copy(isNarration ? sentences.map(function (s) { return s.text; }).join("\n") : text, body); } }, ["Copy"]),
        h("button", { type: "button", onclick: function () { changes.hidden = !changes.hidden; if (!changes.hidden) changes.querySelector("textarea").focus(); } }, ["Ask for changes"])
      ]),
      h("button", { type: "button", class: "primary", onclick: function () {
        UI.choose("I approve this " + noun + ".", { approved: true, job_id: data.id || data.job_id || "" });
      } }, ["Approve " + noun])
    ]));
  }

  /* ------------------------------------------------------------------ publish kit */
  function publishKit(container, kit, opts) {
    opts = opts || {};
    var current = kit.video && kit.video.url ? -1 : 0;  // -1 is the video; 0..n-1 a thumbnail

    function stageSection(items, status, video) {
      var hasVideo = !!(video && video.url);
      if (!hasVideo && current < 0) current = 0;
      if (!items.length && !hasVideo) {
        return section("Video and thumbnails", [h("div", { class: "status" }, [video && video.note ? video.note :
          status && status !== "completed" ? "Thumbnails are " + status.replace(/_/g, " ") + "." : "No video or thumbnails yet."])]);
      }
      var showVideo = hasVideo && current < 0;
      var item = showVideo ? null : (items[current] || items[0]);
      var poster = items.length ? items[0].url : "";
      var stage = showVideo
        ? h("video", { class: "stage-video", src: video.url, poster: poster, controls: "", playsinline: "", preload: "metadata",
            "aria-label": "The finished video" })
        : h("img", { class: "big", src: item.url, alt: (item.label || "Thumbnail") + " preview" });
      var strip = h("div", { class: "thumbs", role: "list" });
      if (hasVideo) {
        strip.appendChild(h("button", { type: "button", class: "card", role: "listitem", "aria-pressed": String(showVideo),
          "aria-label": "Play the video", onclick: function () { current = -1; render(); } },
          [h("span", { class: "thumb-wrap" }, [UI.img(poster, "", "▶"), h("span", { class: "play", "aria-hidden": "true" }, ["▶"])]),
           h("span", { class: "body" }, [h("span", { class: "name" }, ["Video"])])]));
      }
      items.forEach(function (t, i) {
        strip.appendChild(h("button", { type: "button", class: "card", role: "listitem", "aria-pressed": String(!showVideo && i === current),
          "aria-label": "Show " + (t.label || "thumbnail " + (i + 1)), onclick: function () { current = i; render(); } },
          [UI.img(t.url, "", String(i + 1)), h("span", { class: "body" }, [h("span", { class: "name" }, [t.label || "Thumbnail " + (i + 1)])])]));
      });
      var tools = showVideo
        ? [h("button", { type: "button", onclick: function () { open(video.url); } }, ["Open video"])]
        : [h("button", { type: "button", onclick: function () { open(item.url); } }, ["Open full size"]),
           h("button", { type: "button", onclick: function () {
             UI.choose("I will use “" + (item.label || "this thumbnail") + "” as the thumbnail: " + item.url, { thumbnail_url: item.url });
           } }, ["Use this thumbnail"])];
      var children = [stage];
      if (!hasVideo && video && video.note) children.push(h("div", { class: "status" }, [video.note]));
      children.push(strip);
      return section(showVideo ? "Video" : "Thumbnails (" + items.length + ")", children, h("span", { class: "toolbar" }, tools));
    }

    function titlesSection(titles) {
      if (!titles.length) return null;
      var list = h("div", { class: "list", role: "list" });
      titles.forEach(function (t) {
        list.appendChild(h("div", { class: "row title-row", role: "listitem" }, [
          h("div", { class: "main" }, [h("span", { class: "name" }, [t])]),
          h("button", { type: "button", "aria-label": "Copy title: " + t, onclick: function () { copy(t); } }, ["Copy"]),
          h("button", { type: "button", onclick: function () { UI.choose("Use this title for the video: “" + t + "”", { title: t }); } }, ["Use"])
        ]));
      });
      return section("Title ideas", [list]);
    }

    function textSection(title, text) {
      if (!text) return null;
      var pre = h("pre", { class: "copy", tabindex: "0" }, [text]);
      return section(title, [pre], h("button", { type: "button", onclick: function () { copy(text, pre); } }, ["Copy " + title.toLowerCase()]));
    }

    function tagsSection(tags) {
      if (!tags.length) return null;
      var box = h("div", { class: "chips", role: "list" });
      tags.forEach(function (t) { box.appendChild(h("span", { class: "chip tag", role: "listitem" }, [t])); });
      return section("Tags (" + tags.length + ")", [box], h("button", { type: "button", onclick: function () { copy(tags.join(", "), box); } }, ["Copy all tags"]));
    }

    function render() {
      var meta = kit.publish_metadata || {};
      var thumbs = (kit.thumbnails && kit.thumbnails.items) || [];
      var tags = meta.tags || [];
      var description = String(meta.description || "");
      if (tags.length) {
        // The generated description ends with its tags line; show it once, as chips.
        var line = tags.join(", ").toLowerCase();
        description = description.split(/\n\s*\n/).filter(function (p) {
          var flat = p.split(",").map(function (x) { return x.trim().replace(/^#/, "").toLowerCase(); }).filter(Boolean);
          return !(flat.length >= 5 && flat.every(function (x) { return line.indexOf(x) >= 0; }));
        }).join("\n\n");
      }
      var downloads = meta.downloads || {};
      UI.clear(container);
      [stageSection(thumbs, kit.thumbnails && kit.thumbnails.status, kit.video),
       titlesSection(meta.title_variants || []),
       textSection("Description", description.trim()),
       textSection("Chapters", meta.chapters || ""),
       tagsSection(tags),
       downloads.voiceover_url || downloads.project_zip_url ? section("Downloads", [h("div", { class: "toolbar" }, [
         downloads.voiceover_url ? h("button", { type: "button", onclick: function () { open(downloads.voiceover_url); } }, ["Voiceover"]) : null,
         downloads.project_zip_url ? h("button", { type: "button", onclick: function () { open(downloads.project_zip_url); } }, ["Project files"]) : null
       ])]) : null
      ].forEach(function (s) { if (s) container.appendChild(s); });
      if (!container.childNodes.length) UI.status(container, "Nothing to publish yet.");
    }
    render();
  }

  /* ------------------------------------------------------------------ review */
  var TOOL_LABELS = {
    create_job: "Create the video", create_plan_job: "Start the agent-planned video", create_script: "Write the script",
    create_voiceover: "Make the narration audio", continue_job: "Build the full video",
    render_product_video: "Build the product video", draft_product_script: "Draft the product video",
    run_agent: "Edit with Cliptude Agent", render_project: "Export the edited video", create_avatar: "Create the avatar"
  };
  var ACK_TOOLS = { create_job: 1, create_plan_job: 1, create_script: 1, create_voiceover: 1, draft_product_script: 1 };
  var OUTPUT_TOOLS = { "Script only": "create_script", "Narration audio only": "create_voiceover" };

  function isReview(data) {
    var detail = data && data.detail;
    return !!(data && data.status === 428 && detail && typeof detail === "object" && detail.review && detail.review_token);
  }

  /** Which tool returned this review: the server's tag, the host's tool info, else the review's own words. */
  function reviewTool(data) {
    var review = data.detail.review || {};
    var info = Cliptude.state.hostContext && Cliptude.state.hostContext.toolInfo;
    if (data.tool && TOOL_LABELS[data.tool]) return data.tool;
    if (info && info.tool && TOOL_LABELS[info.tool.name]) return info.tool.name;
    if (review.action && TOOL_LABELS[review.action]) return review.action;
    var output = (review.settings || []).filter(function (s) { return s.label === "Output"; })[0];
    if (output) {
      if (OUTPUT_TOOLS[output.value]) return OUTPUT_TOOLS[output.value];
      if (/Agent-planned/.test(output.value)) return "create_plan_job";
      return "create_job";
    }
    return "";
  }

  function reviewSummary(review, tool) {
    if (review.summary) return review.summary;
    var get = function (label) {
      var row = (review.settings || []).filter(function (s) { return s.label === label; })[0];
      return row ? str(row.value) : "";
    };
    var title = get("Title") || get("Avatar name");
    var output = get("Output");
    return (TOOL_LABELS[tool] || "Go ahead") + (output ? ": " + output : "") + (title ? " — “" + title + "”" : "") + ".";
  }

  function creditsBox(credits) {
    if (!credits || typeof credits !== "object") return null;
    var rows = [];
    function add(label, value, cls) {
      if (value === undefined || value === null || value === "") return;
      rows.push(h("div", { class: "credit " + (cls || "") }, [h("span", { class: "k" }, [label]), h("span", { class: "v" }, [typeof value === "number" ? num(value) : String(value)])]));
    }
    add("Credits required", credits.credits_required, "lead");
    add("Held while building", credits.credits_held_while_building);
    add("Already paid", credits.credits_already_paid);
    add("Full video price", credits.full_video_price);
    add("Available", credits.credits_available);
    var enough = credits.enough_credits;
    return h("div", { class: "credits" }, rows.concat(enough === undefined ? [] : [
      h("span", { class: "chip " + (enough ? "ok" : "warn") }, [enough ? "Enough credits" : "Not enough credits"])
    ]));
  }

  function review(container, data, ctx) {
    var detail = data.detail;
    var rev = detail.review || {};
    var tool = reviewTool(data);
    var acks = rev.acknowledgements_required || [];
    var missing = detail.missing || [];
    var summary = reviewSummary(rev, tool);
    var state = { busy: false };

    UI.clear(container);
    container.appendChild(h("div", { class: "head" }, [
      h("h1", {}, ["Review: " + (TOOL_LABELS[tool] || "confirm before Cliptude spends credits")]),
      h("span", { class: "chip info" }, ["Nothing happens until you confirm"])
    ]));
    container.appendChild(h("p", { class: "summary" }, [summary]));
    var out = h("div", { id: "outcome" });  // the result of the confirmed call shows here, at the top
    container.appendChild(out);
    var credits = creditsBox(rev.credits);
    if (credits) container.appendChild(credits);

    (rev.warnings || []).forEach(function (w) {
      container.appendChild(h("div", { class: "callout warn", role: "note" }, [str(w)]));
    });

    var boxes = [];
    if (acks.length) {
      var list = h("div", { class: "list" });
      acks.forEach(function (a) {
        var id = "ack-" + a.id;
        var box = h("input", { type: "checkbox", id: id, value: a.id, onchange: sync });
        boxes.push(box);
        list.appendChild(h("label", { class: "row ack" + (missing.indexOf(a.id) >= 0 ? " missing" : ""), for: id }, [
          box,
          h("span", { class: "main" }, [
            h("span", { class: "name" }, ["I accept: " + a.id.replace(/_/g, " ")]),
            h("span", { class: "desc full" }, [a.message || ""])
          ])
        ]));
      });
      container.appendChild(section("Approve each of these to go on", [list]));
    }

    var table = h("dl", { class: "settings" });
    (rev.settings || []).forEach(function (s) {
      table.appendChild(h("dt", {}, [s.label || ""]));
      var value = str(s.value);
      table.appendChild(h("dd", { class: s.on === false || value === "Off" ? "off" : "" }, [value]));
    });
    container.appendChild(section("Every setting", [table]));

    var confirm = h("button", { type: "button", class: "primary", id: "confirm" }, ["Confirm"]);
    var change = h("button", { type: "button", onclick: function () {
      UI.choose("I want to change something before this runs: " + summary + " Ask me what to change.", { confirmed: false });
    } }, ["Change something"]);
    var note = h("span", { class: "what" }, [""]);
    container.appendChild(h("div", { class: "selection" }, [note, h("span", { class: "toolbar" }, [change, confirm])]));

    function chosen() { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); }
    function sync() {
      var left = boxes.length - chosen().length;
      confirm.disabled = state.busy || left > 0;
      note.textContent = left > 0 ? "Tick " + left + " more to confirm." :
        rev.credits && rev.credits.enough_credits === false ? "Not enough credits: confirming will be refused." : "";
    }
    sync();

    // The only place a widget resends a reviewed call. A real click only; never on load or from data.
    confirm.addEventListener("click", function (event) { confirmReview(event); });
    function confirmReview(event) {
      if (!event || !event.isTrusted || state.busy || confirm.disabled) return;
      var ids = chosen();
      var input = ctx.input();
      if (Cliptude.canCallTools() && tool && input) {
        state.busy = true; sync();
        confirm.textContent = "Confirming…";
        var args = {};
        Object.keys(input).forEach(function (k) { if (k !== "review_token" && k !== "acknowledge") args[k] = input[k]; });
        args.review_token = detail.review_token;
        if (ACK_TOOLS[tool] && ids.length) args.acknowledge = ids;
        Cliptude.callTool(tool, args).then(function (res) {
          if (isReview(res)) { mount(container, res, ctx); UI.toast("The review changed: check it again."); return; }
          result(out, res, tool);
          try { out.scrollIntoView({ block: "nearest" }); } catch (e) {}
          confirm.textContent = UI.errorText(res) ? "Confirm" : "Confirmed";
          if (UI.errorText(res)) { state.busy = false; sync(); return; }
          note.textContent = "Confirmed: " + resultLine(res, tool);
          UI.choose("I confirmed in the review: " + summary + " Result: " + resultLine(res, tool), { confirmed: true });
        }).catch(function () {
          state.busy = false; confirm.textContent = "Confirm"; sync();
          sendConfirm(ids);
        });
      } else {
        sendConfirm(ids);
      }
    }
    function sendConfirm(ids) {
      UI.choose("I confirm: " + summary + " review_token=" + detail.review_token +
        (ids.length ? " acknowledge=" + ids.join(",") : ""), { confirmed: true, review_token: detail.review_token, acknowledge: ids });
    }
  }

  /* ------------------------------------------------------------------ result card */
  var NEXT = {
    create_job: "Claude checks it with get_job; each check updates the card.",
    create_script: "Claude checks it with get_job; the script shows here to approve when it is written.",
    create_voiceover: "Claude checks it with get_job until the narration is ready.",
    create_plan_job: "Cliptude voices the narration; then Claude plans every visual.",
    continue_job: "Claude checks it with get_job; each check updates the card.",
    render_product_video: "Cliptude builds the video; Claude checks it with get_product_video_status.",
    draft_product_script: "Claude checks it with get_product_video_status.",
    run_agent: "Cliptude Agent is editing; Claude follows it with get_agent_run.",
    render_project: "The export is rendering; Claude checks it with get_render.",
    create_avatar: "HeyGen finishes the avatar in a few minutes; it then shows under your avatars."
  };

  function pick(data) {
    var inner = data.run || data.render || data.avatar || data.job || data;
    return {
      id: String(data.id || data.job_id || inner.id || ""),
      status: String(inner.status || data.status || data.stage || ""),
      title: String(data.title || inner.title || inner.name || ""),
      kind: data.run ? "Agent run" : data.render ? "Export" : data.avatar ? "Avatar" : "Job"
    };
  }
  function resultLine(data, tool) {
    var p = pick(data || {});
    return p.kind + (p.id ? " " + p.id : "") + (p.status ? " is " + p.status.replace(/_/g, " ") : "") + ".";
  }

  function result(container, data, tool) {
    UI.clear(container);
    var err = UI.errorText(data);
    if (err) {
      var detail = (data && data.detail) || {};
      container.appendChild(h("div", { class: "status error", role: "alert" }, [err]));
      if (detail.billing_url) container.appendChild(h("div", { class: "toolbar" }, [
        h("button", { type: "button", class: "primary", onclick: function () { open(detail.billing_url); } }, ["Add credits"])
      ]));
      return;
    }
    var p = pick(data);
    var chips = [h("span", { class: "chip info" }, [p.status ? p.status.replace(/_/g, " ") : "started"])];
    if (data.estimated_minutes) chips.push(h("span", { class: "chip" }, ["≈ " + data.estimated_minutes + " min"]));
    if (data.credits_reserved) chips.push(h("span", { class: "chip" }, [num(data.credits_reserved) + " credits held"]));
    container.appendChild(h("div", { class: "row result" }, [
      h("div", { class: "main" }, [
        h("span", { class: "name" }, [p.title || p.kind]),
        p.id ? h("span", { class: "muted" }, [p.kind + " " + p.id]) : null,
        h("span", { class: "chips" }, chips),
        NEXT[tool] ? h("span", { class: "desc full" }, [NEXT[tool]]) : null
      ]),
      data.editor_url ? h("button", { type: "button", onclick: function () { open(data.editor_url); } }, ["Open editor"]) : null
    ]));
  }

  /* ------------------------------------------------------------------ progress */
  var STATUS_LABELS = {
    "not-started": "Not started", saved: "Saved", pending: "Pending", queued: "Queued", running: "Working",
    awaiting_project: "Waiting for the project", ready_for_edit: "Ready to review", plan_ready: "Plan ready",
    completed: "Completed", failed: "Failed", revoked: "Cancelled",
    script_drafting: "Writing the script", script_ready: "Script ready", voicing: "Voicing the narration",
    producing: "Building the video", ready_for_review: "Ready to review", rendered: "Rendered", cancelled: "Cancelled"
  };
  var kitLoaded = {};

  function isReady(job) {
    var stage = job.stage || "";
    if (stage) return stage === "ready_for_review" || stage === "rendered";
    var kind = job.output_kind || "video";
    return (job.status === "ready_for_edit" || job.status === "completed") && (kind === "video" || kind === "plan");
  }

  function progress(container, job) {
    var id = String(job.id || job.job_id || "");
    var stage = job.stage || job.status || "";
    var label = STATUS_LABELS[stage] || String(stage).replace(/_/g, " ");
    var pct = Math.max(0, Math.min(100, Number(job.progress || 0)));
    var failed = stage === "failed" || job.status === "failed";
    var ready = isReady(job);
    var done = ready || failed || job.status === "completed" || stage === "plan_ready" || stage === "script_ready";
    if (ready) pct = 100;

    UI.clear(container);
    container.appendChild(h("div", { class: "head" }, [
      h("h1", {}, [job.title || (job.product && job.product.title) || "Video job"]),
      h("span", { class: "chip " + (failed ? "warn" : ready ? "ok" : "info") }, [label])
    ]));
    if (!failed) {
      container.appendChild(h("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(Math.round(pct)), "aria-label": "Progress" }, [h("span", { style: { width: pct + "%" } })]));
    }
    var facts = [];
    if (!done && job.current_step) facts.push(h("span", {}, [String(job.current_step)]));
    if (!done) facts.push(h("span", { class: "muted" }, [Math.round(pct) + "%"]));
    if (job.estimated_minutes && !done) facts.push(h("span", { class: "muted" }, ["≈ " + job.estimated_minutes + " min"]));
    if (job.duration) facts.push(h("span", { class: "muted" }, [job.duration + " min video"]));
    if (id) facts.push(h("span", { class: "muted" }, ["Job " + id.slice(0, 8)]));
    container.appendChild(h("div", { class: "facts" }, facts));
    if (failed) container.appendChild(h("div", { class: "status error", role: "alert" }, [String(job.error || job.current_step || "The job failed.")]));
    if (job.warning) container.appendChild(h("div", { class: "callout warn", role: "note" }, [job.warning]));
    if (!done) container.appendChild(h("p", { class: "muted" }, ["Claude checks on the job every so often; each check shows the latest here."]));

    var voice = job.voiceover && job.voiceover.url;
    if (voice) container.appendChild(section("Narration audio", [h("audio", { src: voice, controls: "", preload: "none", class: "audio" })]));
    if (stage === "plan_ready") container.appendChild(h("p", { class: "summary" }, ["The narration is voiced. Claude plans every visual next, then builds the video."]));

    if (!ready || !id) return;
    var kitBox = h("div", { class: "kit" });
    container.appendChild(kitBox);
    function showKitButton() {
      UI.clear(kitBox).appendChild(h("div", { class: "toolbar" }, [
        job.editor_url ? h("button", { type: "button", onclick: function () { open(job.editor_url); } }, ["Open editor"]) : null,
        h("button", { type: "button", class: "primary", onclick: function () {
          UI.choose("Show the publish kit for job " + id + ".", { job_id: id });
        } }, ["Show publish kit"])
      ]));
    }
    if (!Cliptude.canCallTools()) { showKitButton(); return; }
    // Ready: fetch the publish kit once (free, read-mostly: it opens the editor project) and show it here.
    UI.status(kitBox, "Loading the publish kit…");
    var pending = kitLoaded[id] || (kitLoaded[id] = Cliptude.callTool("show_publish_kit", { job_id: id }));
    pending.then(function (kit) {
      if (!kit || UI.errorText(kit)) { showKitButton(); return; }
      publishKit(kitBox, kit);
    }).catch(showKitButton);
  }

  /* ------------------------------------------------------------------ dispatch */
  function isScript(data) {
    if (Array.isArray(data.sentences) && data.sentences.length && typeof data.sentences[0] === "object") return true;
    if (typeof data.script !== "string" || !data.script.trim()) return false;
    if (data.stage) return data.stage === "script_ready";
    return (data.output_kind === "script" && data.status === "completed") || !data.status;
  }

  function mount(container, data, ctx) {
    ctx = ctx || { input: function () { return null; } };
    if (isReview(data)) { review(container, data, ctx); return "review"; }
    var err = UI.errorText(data);
    var info = Cliptude.state.hostContext && Cliptude.state.hostContext.toolInfo;
    ctx.tool = ctx.tool || data.tool || (info && info.tool && info.tool.name) || "";
    // A failed job carries its error too (status "failed"); API errors have a numeric status or none.
    var isJob = typeof data.status === "string" && (data.id || data.job_id);
    if (err && !isJob) { result(container, data, ctx.tool); return "error"; }
    if (isScript(data)) { script(container, data, ctx); return "script"; }
    if (ctx.mode === "job") { progress(container, data); return "progress"; }
    result(container, data, ctx.tool);
    return "result";
  }

  return { mount: mount, review: review, script: script, progress: progress, result: result, publishKit: publishKit,
           copy: copy, isReview: isReview, budgetLine: budgetLine };
})();
