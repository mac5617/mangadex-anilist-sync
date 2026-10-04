/* Recommendation map: a small force-directed layout in plain SVG (no library).
   Nodes: rec (circle), series (square), theme (pill), creator (diamond). Every node is labelled, so
   identity never rests on colour alone; the table under the map lists the same links. */
(function () {
  "use strict";
  var svg = document.getElementById("rec-graph");
  var dataEl = document.getElementById("graph-data");
  if (!svg || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var tip = document.getElementById("graph-tip");
  var NS = "http://www.w3.org/2000/svg";
  var KIND_NAMES = { rec: "Recommendation", series: "On your list", theme: "Genre or tag", creator: "Creator" };

  var width = Math.max(320, svg.parentElement.clientWidth - 2);
  var height = Math.round(Math.min(760, Math.max(460, width * 0.62)));
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.style.height = "auto";

  var byId = {};
  var nodes = data.nodes.map(function (n, i) {
    var o = Object.assign({}, n);
    o.r = n.kind === "rec" ? 6 + 9 * n.size : 5 + 7 * n.size;
    // Start recommendations in the middle and hubs on a ring around them, so the layout settles fast.
    var angle = (i / data.nodes.length) * Math.PI * 2;
    var ring = n.kind === "rec" ? 0.22 : 0.46;   // an ellipse matching the panel's shape
    o.x = width / 2 + Math.cos(angle) * ring * width;
    o.y = height / 2 + Math.sin(angle) * ring * height;
    o.vx = 0; o.vy = 0; o.links = [];
    byId[o.id] = o;
    return o;
  });
  var links = data.links.filter(function (l) { return byId[l.source] && byId[l.target]; }).map(function (l) {
    var link = { s: byId[l.source], t: byId[l.target], kind: l.kind };
    link.s.links.push(link); link.t.links.push(link);
    return link;
  });

  // ---- drawing -----------------------------------------------------------------------
  function el(name, attrs, parent) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  var gLinks = el("g", { "class": "g-links" }, svg);
  var gNodes = el("g", { "class": "g-nodes" }, svg);
  links.forEach(function (l) { l.el = el("line", { "class": "g-link g-link-" + l.kind }, gLinks); });
  nodes.forEach(function (n) {
    var g = el("g", { "class": "g-node g-" + n.kind, tabindex: "0", role: "link" }, gNodes);
    g.setAttribute("aria-label", KIND_NAMES[n.kind] + ": " + n.label);
    var r = n.r, shape;
    if (n.kind === "rec") shape = el("circle", { r: r }, g);
    else if (n.kind === "series") shape = el("rect", { x: -r, y: -r, width: 2 * r, height: 2 * r, rx: 2 }, g);
    else if (n.kind === "creator") shape = el("rect", { x: -r * 0.8, y: -r * 0.8, width: r * 1.6, height: r * 1.6, transform: "rotate(45)" }, g);
    else shape = el("rect", { x: -r * 1.4, y: -r * 0.7, width: r * 2.8, height: r * 1.4, rx: r * 0.7 }, g);
    shape.setAttribute("class", "g-shape");
    var label = n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label;
    var text = el("text", { x: r + 4, y: 4, "class": "g-label" }, g);
    text.textContent = (n.kind === "rec" ? n.rank + ". " : "") + label;
    n.el = g;
  });

  // ---- simulation --------------------------------------------------------------------
  // Forces scale with the panel: each node gets about area / n of room, so the map fills its space.
  var spacing = Math.sqrt((width * height) / Math.max(nodes.length, 1));
  var REPEL = spacing * spacing * 0.8, LINK = spacing * 1.05;
  var alpha = 1;
  function tick() {
    var i, j, a, b, dx, dy, d2, d, f;
    for (i = 0; i < nodes.length; i++) {          // repulsion, plus a stronger push when shapes overlap
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        dx = b.x - a.x; dy = b.y - a.y; d2 = dx * dx + dy * dy || 0.01; d = Math.sqrt(d2);
        f = REPEL / d2;
        var min = a.r + b.r + 22;
        if (Math.abs(dy) < 16 && Math.abs(dx) < 150) f += 9 / (Math.abs(dy) + 1);  // keep labels on different lines
        if (d < min) f += (min - d) * 0.35;
        dx /= d; dy /= d;
        a.vx -= dx * f * alpha; a.vy -= dy * f * alpha;
        b.vx += dx * f * alpha; b.vy += dy * f * alpha;
      }
    }
    links.forEach(function (l) {                  // springs
      dx = l.t.x - l.s.x; dy = l.t.y - l.s.y; d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      f = (d - LINK) * 0.03 * alpha;
      dx /= d; dy /= d;
      l.s.vx += dx * f; l.s.vy += dy * f; l.t.vx -= dx * f; l.t.vy -= dy * f;
    });
    nodes.forEach(function (n) {                  // gravity, damping, bounds
      n.vx += (width / 2 - n.x) * 0.0003 * alpha; n.vy += (height / 2 - n.y) * 0.0035 * alpha;
      if (n.fixed) { n.vx = 0; n.vy = 0; return; }
      n.vx *= 0.6; n.vy *= 0.6;
      n.x = Math.max(n.r + 4, Math.min(width - n.r - 90, n.x + n.vx));
      n.y = Math.max(n.r + 4, Math.min(height - n.r - 4, n.y + n.vy));
    });
    alpha *= 0.99;
  }
  function draw() {
    links.forEach(function (l) {
      l.el.setAttribute("x1", l.s.x); l.el.setAttribute("y1", l.s.y);
      l.el.setAttribute("x2", l.t.x); l.el.setAttribute("y2", l.t.y);
    });
    nodes.forEach(function (n) { n.el.setAttribute("transform", "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")"); });
  }
  var running = false;
  function run() {
    if (running) return;
    running = true;
    (function frame() {
      for (var k = 0; k < 3; k++) tick();
      draw();
      if (alpha > 0.02) requestAnimationFrame(frame); else running = false;
    })();
  }
  // Settle most of the way before the first paint, then animate the rest.
  for (var k = 0; k < 300; k++) tick();
  draw();
  run();

  // ---- interaction -------------------------------------------------------------------
  function focus(n) {
    var near = {}; near[n.id] = true;
    n.links.forEach(function (l) { near[l.s.id] = true; near[l.t.id] = true; });
    svg.classList.add("focused");
    nodes.forEach(function (m) { m.el.classList.toggle("near", !!near[m.id]); });
    links.forEach(function (l) { l.el.classList.toggle("near", l.s === n || l.t === n); });
    var others = n.links.map(function (l) { return (l.s === n ? l.t : l.s).label; });
    tip.textContent = "";
    var strong = document.createElement("strong"); strong.textContent = n.label; tip.appendChild(strong);
    var sub = document.createElement("span");
    sub.textContent = KIND_NAMES[n.kind] + (n.count ? " · " + n.count + " on your list" : "") +
      (others.length ? " · linked to " + others.slice(0, 6).join(", ") + (others.length > 6 ? "…" : "") : "");
    tip.appendChild(sub);
    tip.hidden = false;
  }
  function blur() {
    svg.classList.remove("focused");
    nodes.forEach(function (m) { m.el.classList.remove("near"); });
    links.forEach(function (l) { l.el.classList.remove("near"); });
    tip.hidden = true;
  }
  function placeTip(evt) {
    var box = svg.parentElement.getBoundingClientRect();
    tip.style.left = Math.min(box.width - 260, evt.clientX - box.left + 14) + "px";
    tip.style.top = (evt.clientY - box.top + 14) + "px";
  }
  function point(evt) {
    var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  var dragging = null, moved = false;
  nodes.forEach(function (n) {
    n.el.addEventListener("mouseenter", function (e) { if (!dragging) { focus(n); placeTip(e); } });
    n.el.addEventListener("mousemove", function (e) { if (!dragging) placeTip(e); });
    n.el.addEventListener("mouseleave", function () { if (!dragging) blur(); });
    n.el.addEventListener("focus", function () {
      focus(n);
      var box = svg.getBoundingClientRect(), scale = box.width / width;
      tip.style.left = Math.min(box.width - 260, n.x * scale + 14) + "px"; tip.style.top = (n.y * scale + 14) + "px";
    });
    n.el.addEventListener("blur", blur);
    n.el.addEventListener("keydown", function (e) { if (e.key === "Enter" && n.url) window.open(n.url, "_blank", "noopener"); });
    n.el.addEventListener("pointerdown", function (e) {
      dragging = n; moved = false; n.fixed = true;
      n.el.setPointerCapture(e.pointerId);
    });
    n.el.addEventListener("pointermove", function (e) {
      if (dragging !== n) return;
      var p = point(e);
      if (Math.abs(p.x - n.x) + Math.abs(p.y - n.y) > 2) moved = true;
      n.x = p.x; n.y = p.y; alpha = Math.max(alpha, 0.25); draw(); run();
    });
    n.el.addEventListener("pointerup", function () {
      dragging = null; n.fixed = false;
      if (!moved && n.url) window.open(n.url, "_blank", "noopener");
    });
  });
})();
