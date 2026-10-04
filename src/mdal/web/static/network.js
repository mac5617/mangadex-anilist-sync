/* Network graphs (Discover map, Stats connections), laid out with d3-force (static/vendor).
 *
 * Mounts every <svg data-network="json-id">. Data: {nodes: [{id, label, kind, size 0-1, image?, href?,
 * external?, tone?, tip: [lines], rank?}], links: [{source, target, weight 0-1}]}.
 * Node kinds: "rec" and "series" are cover images, "theme" and "creator" are labelled pills, "tag" is a
 * circle sized by size and shaded by tone (1-5). The layout is settled before the first paint and then
 * zoomed to fit, so the graph always fills its panel. Wheel or the +/- buttons zoom, dragging empty space
 * pans, dragging a node moves it, hovering traces its links, clicking opens it.
 */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var uid = 0;

  function el(name, attrs, parent) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function clip(text, n) { return text.length > n ? text.slice(0, n - 1) + "…" : text; }

  function mount(svg) {
    var source = document.getElementById(svg.dataset.network);
    if (!source || !window.d3 || !d3.forceSimulation) return;
    var data = JSON.parse(source.textContent);
    if (!data.nodes.length) return;
    var panel = svg.closest(".network-panel") || svg.parentElement;
    var tip = panel.querySelector(".network-tip");
    var id = "net" + (++uid);

    var width = Math.max(320, Math.round(svg.parentElement.clientWidth));
    var height = Math.round(Math.min(820, Math.max(460, width * 0.6)));
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);

    var defs = el("defs", {}, svg);
    var clipPath = el("clipPath", { id: id + "-clip", clipPathUnits: "objectBoundingBox" }, defs);
    el("circle", { cx: 0.5, cy: 0.5, r: 0.5 }, clipPath);
    var viewport = el("g", { "class": "net-viewport" }, svg);
    var gLinks = el("g", { "class": "net-links" }, viewport);
    var gNodes = el("g", { "class": "net-nodes" }, viewport);

    var byId = {};
    var nodes = data.nodes.map(function (n) { var o = Object.assign({}, n); byId[o.id] = o; o.near = []; return o; });
    var links = data.links.filter(function (l) { return byId[l.source] && byId[l.target]; })
      .map(function (l) { return { source: byId[l.source], target: byId[l.target], weight: l.weight || 0.5, kind: l.kind }; });
    links.forEach(function (l) { l.source.near.push(l); l.target.near.push(l); });

    // ---- draw nodes (sizes are measured after drawing, for collision) ----------------------
    links.forEach(function (l) {
      l.el = el("path", { "class": "net-link" + (l.kind ? " net-link-" + l.kind : ""),
                          "stroke-width": (0.8 + 2.2 * l.weight).toFixed(2) }, gLinks);
    });
    nodes.forEach(function (n) {
      var g = el("g", { "class": "net-node net-" + n.kind + (n.tone !== undefined ? " tone-" + n.tone : ""), tabindex: 0,
                        role: n.href ? "link" : "img", "aria-label": (n.tip || [n.label]).join(", ") }, gNodes);
      n.el = g;
      if (n.kind === "rec" || n.kind === "series") {
        n.r = n.kind === "rec" ? 15 + 11 * (n.size || 0.5) : 12;
        el("circle", { r: n.r + 2, "class": "net-ring" }, g);
        el("circle", { r: n.r, "class": "net-fill" }, g);
        if (n.image) {
          el("image", { href: n.image, x: -n.r, y: -n.r, width: 2 * n.r, height: 2 * n.r,
                        "clip-path": "url(#" + id + "-clip)", preserveAspectRatio: "xMidYMid slice" }, g);
        }
        var text = el("text", { y: n.r + 13, "class": "net-label" }, g);
        text.textContent = (n.rank ? n.rank + ". " : "") + clip(n.label, n.kind === "rec" ? 22 : 18);
        n.lw = text.getComputedTextLength();
        n.radius = Math.max(n.r + 6, n.lw / 2 + 2);
        n.below = 16;
      } else if (n.kind === "theme" || n.kind === "creator") {
        var t = el("text", { y: 4, "class": "net-pill-text" }, g);
        t.textContent = clip(n.label, 24);
        var w = t.getComputedTextLength() + 16, h = 21;
        g.insertBefore(el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: h / 2, "class": "net-pill" }), t);
        n.r = h / 2; n.radius = w / 2 + 3; n.below = 0;
      } else {
        n.r = 5 + 20 * Math.sqrt(n.size || 0.1);
        el("circle", { r: n.r, "class": "net-dot" }, g);
        var lt = el("text", { y: n.r + 12, "class": "net-label" + (n.size > 0.35 ? " strong" : "") }, g);
        lt.textContent = clip(n.label, 20);
        n.lw = lt.getComputedTextLength();
        n.radius = Math.max(n.r + 3, n.lw / 2 + 2);
        n.below = 14;
      }
    });

    // ---- layout ----------------------------------------------------------------------------
    var sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).distance(function (l) {
        return (l.source.radius + l.target.radius) * 0.6 + 40 + (1 - l.weight) * 60;
      }).strength(function (l) {
        return (0.25 + 0.6 * l.weight) / Math.min(l.source.near.length, l.target.near.length);
      }))
      .force("charge", d3.forceManyBody().strength(function (n) { return -120 - 6 * n.radius; }).distanceMax(500))
      .force("collide", d3.forceCollide(function (n) { return n.radius + 4; }).iterations(3))
      // Pull towards the centre in proportion to the panel's shape, so the layout comes out as wide as the panel.
      .force("x", d3.forceX(width / 2).strength(0.08 * height / width))
      .force("y", d3.forceY(height / 2).strength(0.08))
      .stop();
    for (var i = 0; i < 420; i++) sim.tick();
    // A force layout settles roughly round; stretch it sideways to the panel's shape, then let collisions
    // relax for a moment. Zoom-to-fit then fills the width as well as the height.
    var xs = nodes.map(function (n) { return n.x; }), ys = nodes.map(function (n) { return n.y; });
    var spanX = Math.max.apply(null, xs) - Math.min.apply(null, xs) || 1;
    var spanY = Math.max.apply(null, ys) - Math.min.apply(null, ys) || 1;
    var stretch = Math.min(1.8, Math.max(1, (width / height) / (spanX / spanY)));
    if (stretch > 1.02) {
      var cx = (Math.max.apply(null, xs) + Math.min.apply(null, xs)) / 2;
      nodes.forEach(function (n) { n.x = cx + (n.x - cx) * stretch; });
      sim.force("x").strength(0); sim.force("y").strength(0.02);
      sim.alpha(0.12);
      for (var j = 0; j < 80; j++) sim.tick();
    }

    function linkPath(l) {
      var x1 = l.source.x, y1 = l.source.y, x2 = l.target.x, y2 = l.target.y;
      var mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = x2 - x1, dy = y2 - y1;
      return "M" + x1.toFixed(1) + "," + y1.toFixed(1) + " Q" + (mx - dy * 0.12).toFixed(1) + "," +
        (my + dx * 0.12).toFixed(1) + " " + x2.toFixed(1) + "," + y2.toFixed(1);
    }
    function draw() {
      links.forEach(function (l) { l.el.setAttribute("d", linkPath(l)); });
      nodes.forEach(function (n) { n.el.setAttribute("transform", "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")"); });
    }
    sim.on("tick", draw);

    // ---- zoom and pan ----------------------------------------------------------------------
    var view = { k: 1, x: 0, y: 0 };
    function apply() { viewport.setAttribute("transform", "translate(" + view.x.toFixed(1) + "," + view.y.toFixed(1) + ") scale(" + view.k.toFixed(3) + ")"); }
    function fit() {
      var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      nodes.forEach(function (n) {
        if (n.el.classList.contains("off")) return;
        var half = Math.max(n.r, (n.lw || 0) / 2);
        x0 = Math.min(x0, n.x - half); x1 = Math.max(x1, n.x + half);
        y0 = Math.min(y0, n.y - n.r); y1 = Math.max(y1, n.y + n.r + (n.below || 0));
      });
      var pad = 18, k = Math.min((width - 2 * pad) / (x1 - x0), (height - 2 * pad) / (y1 - y0), 2);
      view.k = k; view.x = (width - k * (x0 + x1)) / 2; view.y = (height - k * (y0 + y1)) / 2;
      apply();
    }
    function zoomAt(factor, px, py) {
      var k = Math.max(0.3, Math.min(5, view.k * factor));
      view.x = px - (px - view.x) * (k / view.k); view.y = py - (py - view.y) * (k / view.k); view.k = k;
      apply();
    }
    function point(evt) {
      var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      return pt.matrixTransform(svg.getScreenCTM().inverse());
    }
    function graphPoint(evt) { var p = point(evt); return { x: (p.x - view.x) / view.k, y: (p.y - view.y) / view.k }; }
    draw(); fit();

    svg.addEventListener("wheel", function (e) {
      e.preventDefault();
      var p = point(e); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, p.x, p.y);
    }, { passive: false });
    panel.querySelectorAll("[data-net-zoom]").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = b.dataset.netZoom;
        if (a === "fit") fit(); else zoomAt(a === "in" ? 1.3 : 1 / 1.3, width / 2, height / 2);
      });
    });

    // ---- focus and tooltip --------------------------------------------------------------------
    function focus(n) {
      svg.classList.add("focusing");
      var keep = {}; keep[n.id] = true;
      n.near.forEach(function (l) { keep[l.source.id] = true; keep[l.target.id] = true; l.el.classList.add("near"); });
      nodes.forEach(function (m) { m.el.classList.toggle("near", !!keep[m.id]); });
      if (!tip) return;
      tip.textContent = "";
      (n.tip || [n.label]).forEach(function (line, i) {
        var e = document.createElement(i ? "span" : "strong"); e.textContent = line; tip.appendChild(e);
      });
      tip.hidden = false;
    }
    function blur() {
      svg.classList.remove("focusing");
      nodes.forEach(function (m) { m.el.classList.remove("near"); });
      links.forEach(function (l) { l.el.classList.remove("near"); });
      if (tip) tip.hidden = true;
    }
    function placeTip(x, y) {
      if (!tip) return;
      var box = panel.getBoundingClientRect();
      tip.style.left = Math.max(8, Math.min(box.width - tip.offsetWidth - 8, x - box.left + 14)) + "px";
      tip.style.top = Math.min(box.height - tip.offsetHeight - 8, y - box.top + 16) + "px";
    }
    function open(n) {
      if (!n.href) return;
      if (n.external) window.open(n.href, "_blank", "noopener"); else window.location.href = n.href;
    }

    // ---- dragging nodes, panning the background -------------------------------------------------
    var drag = null;
    nodes.forEach(function (n) {
      n.el.addEventListener("pointerenter", function (e) { if (!drag) { focus(n); placeTip(e.clientX, e.clientY); } });
      n.el.addEventListener("pointermove", function (e) { if (!drag) placeTip(e.clientX, e.clientY); });
      n.el.addEventListener("pointerleave", function () { if (!drag) blur(); });
      n.el.addEventListener("focus", function () {
        focus(n); var r = n.el.getBoundingClientRect(); placeTip(r.right, r.bottom);
      });
      n.el.addEventListener("blur", blur);
      n.el.addEventListener("keydown", function (e) { if (e.key === "Enter") open(n); });
      n.el.addEventListener("pointerdown", function (e) {
        e.stopPropagation();
        drag = { node: n, moved: false, start: point(e) };
        n.el.setPointerCapture(e.pointerId);
      });
      n.el.addEventListener("pointermove", function (e) {
        if (!drag || drag.node !== n) return;
        var p = point(e);
        if (!drag.moved && Math.hypot(p.x - drag.start.x, p.y - drag.start.y) < 4) return;
        if (!drag.moved) { drag.moved = true; sim.alphaTarget(0.15).restart(); }
        var g = graphPoint(e); n.fx = g.x; n.fy = g.y;
      });
      n.el.addEventListener("pointerup", function () {
        if (!drag || drag.node !== n) return;
        var moved = drag.moved; drag = null;
        if (moved) { sim.alphaTarget(0); n.fx = null; n.fy = null; } else open(n);
      });
    });
    var pan = null;
    svg.addEventListener("pointerdown", function (e) {
      pan = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      svg.setPointerCapture(e.pointerId); svg.classList.add("panning");
    });
    svg.addEventListener("pointermove", function (e) {
      if (!pan) return;
      var scale = width / svg.getBoundingClientRect().width;
      view.x = pan.vx + (e.clientX - pan.x) * scale; view.y = pan.vy + (e.clientY - pan.y) * scale; apply();
    });
    svg.addEventListener("pointerup", function () { pan = null; svg.classList.remove("panning"); });
    svg.addEventListener("dblclick", fit);

    // ---- legend toggles: show or hide one kind ---------------------------------------------------
    panel.querySelectorAll("[data-net-kind]").forEach(function (b) {
      b.addEventListener("click", function () {
        var kind = b.dataset.netKind, on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", on ? "true" : "false");
        nodes.forEach(function (n) { if (n.kind === kind) n.el.classList.toggle("off", !on); });
        links.forEach(function (l) {
          l.el.classList.toggle("off", l.source.el.classList.contains("off") || l.target.el.classList.contains("off"));
        });
      });
    });
  }

  function init() { document.querySelectorAll("svg[data-network]").forEach(mount); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
