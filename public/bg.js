(function () {
  var canvas = document.getElementById('bg-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var W = window.innerWidth, H = window.innerHeight;

  function resize() {
    var dpr = window.devicePixelRatio || 1;
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width  = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener('resize', resize);

  var FLAME = [
    'rgba(255, 55,  10, 0.95)',
    'rgba(255,130,  20, 0.90)',
    'rgba(255,185,  50, 0.82)',
    'rgba(240,230,  90, 0.65)',
    'rgba(255,255, 210, 0.40)',
  ];
  var ENV_FILL   = 'rgba(200,184,154,0.22)';
  var ENV_STROKE = 'rgba(200,184,154,0.42)';

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function makeParticle(envW) {
    var ml = rnd(0.55, 1.0);
    return {
      ox:      rnd(-envW * 0.275, envW * 0.275),
      phase:   rnd(0, Math.PI * 2),
      speed:   rnd(0.4, 0.9),
      life:    rnd(0, ml),   // always < maxLife — prevents negative arc radius
      maxLife: ml,
      size:    rnd(1.5, 4.0),
      tier:    Math.floor(rnd(0, FLAME.length)),
    };
  }

  function Envelope(initial) { this.init(initial !== false); }

  Envelope.prototype.init = function (initial) {
    this.w  = rnd(36, 68);
    this.h  = this.w * 0.65;
    this.x  = rnd(0, W);
    this.y  = initial
      ? rnd(-this.h, H + this.h)
      : H + this.h + rnd(20, H * 0.7);
    this.vy = -rnd(0.15, 0.37);
    this.vx = rnd(-0.18, 0.18);
    this.wob = rnd(0, Math.PI * 2);
    this.wobSpeed = rnd(0.008, 0.020);
    this.rot = rnd(-0.35, 0.35);
    this.rotSpeed = rnd(-0.003, 0.003);
    this.alpha  = initial ? rnd(0, 0.9) : 0;
    this.fadeIn = rnd(0.004, 0.007);
    var n = Math.floor(rnd(20, 34));
    this.pts = [];
    for (var i = 0; i < n; i++) this.pts.push(makeParticle(this.w));
  };

  Envelope.prototype.update = function (dt) {
    var s = dt * 60;
    this.wob += this.wobSpeed * s;
    this.rot += this.rotSpeed * s;
    this.x   += (this.vx + Math.sin(this.wob) * 0.25) * s;
    this.y   += this.vy * s;
    this.alpha = Math.min(1, this.alpha + this.fadeIn * s);

    for (var i = 0; i < this.pts.length; i++) {
      var p = this.pts[i];
      p.life += p.speed * dt;
      if (p.life >= p.maxLife) {
        var np = makeParticle(this.w);
        p.ox = np.ox; p.phase = np.phase; p.speed = np.speed;
        p.maxLife = np.maxLife; p.size = np.size; p.tier = np.tier;
        p.life = 0;
      }
    }

    if (this.y < -this.h - 80) this.init(false);
  };

  Envelope.prototype.draw = function () {
    var hw = this.w / 2, hh = this.h / 2;

    // --- Compute back direction (propulsion trail = opposite to velocity) ---
    // The current horizontal component includes wobble for realism.
    var effVX = this.vx + Math.sin(this.wob) * 0.25;
    var effVY = this.vy;
    var vLen  = Math.sqrt(effVX * effVX + effVY * effVY) || 1;
    // World-space unit vector pointing *away* from travel direction
    var wbX = -effVX / vLen;
    var wbY = -effVY / vLen;
    // Rotate into local (envelope) space — canvas is already rotated by this.rot
    var cr = Math.cos(-this.rot), sr = Math.sin(-this.rot);
    var lbX = cr * wbX - sr * wbY;  // local back-direction X
    var lbY = sr * wbX + cr * wbY;  // local back-direction Y

    // Emission origin: centre of the trailing edge
    var emitX = lbX * hw;
    var emitY = lbY * hh;

    ctx.save();
    ctx.globalAlpha = this.alpha;
    ctx.translate(this.x, this.y);
    ctx.rotate(this.rot);

    // Envelope body
    ctx.beginPath();
    ctx.rect(-hw, -hh, this.w, this.h);
    ctx.fillStyle = ENV_FILL;
    ctx.fill();
    ctx.strokeStyle = ENV_STROKE;
    ctx.lineWidth = 0.8;
    ctx.stroke();

    // Top flap V
    ctx.beginPath();
    ctx.moveTo(-hw, -hh);
    ctx.lineTo(0, 0);
    ctx.lineTo(hw, -hh);
    ctx.stroke();

    // Bottom crease V
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(-hw, hh);
    ctx.lineTo(0, 0);
    ctx.lineTo(hw, hh);
    ctx.stroke();

    // Glow halo behind the envelope (at emission origin, extending backward)
    var glowCX = emitX + lbX * this.h * 0.5;
    var glowCY = emitY + lbY * this.h * 0.5;
    var gr = this.h * 0.9;
    var grd = ctx.createRadialGradient(glowCX, glowCY, 0, glowCX, glowCY, gr);
    grd.addColorStop(0, 'rgba(255,110,20,0.22)');
    grd.addColorStop(1, 'rgba(255,110,20,0)');
    ctx.globalAlpha = this.alpha * 0.75;
    ctx.beginPath();
    ctx.arc(glowCX, glowCY, gr, 0, Math.PI * 2);
    ctx.fillStyle = grd;
    ctx.fill();

    // Flame particles — trail in the back direction
    for (var i = 0; i < this.pts.length; i++) {
      var p   = this.pts[i];
      var t   = p.life / p.maxLife;
      var sz  = Math.max(0, p.size * (1 - t * 0.7));
      if (sz === 0) continue;

      // Lateral spread perpendicular to the back-direction, shrinks as t grows
      var spread = p.ox * (1 - t * 0.6);
      // Sinusoidal flicker perpendicular to trail axis
      var flicker = Math.sin(p.phase + p.life * 6) * 4 * t;
      // px/py in local space: emit from trailing edge, extend further back with t
      var trailDist = t * this.h * 1.8;
      var px = emitX + spread * lbY + flicker * lbY + trailDist * lbX;
      var py = emitY - spread * lbX - flicker * lbX + trailDist * lbY;

      var ti = Math.min(p.tier + Math.floor(t * 2), FLAME.length - 1);
      ctx.globalAlpha = this.alpha * (1 - t) * 0.85;
      ctx.beginPath();
      ctx.arc(px, py, sz, 0, Math.PI * 2);
      ctx.fillStyle = FLAME[ti];
      ctx.fill();
    }

    ctx.restore();
  };

  var COUNT     = 22;
  var envelopes = [];
  for (var i = 0; i < COUNT; i++) envelopes.push(new Envelope(true));

  var lastTime = null;
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) lastTime = null;
  });

  function loop(now) {
    if (lastTime === null) lastTime = now;
    var dt = Math.min((now - lastTime) / 1000, 0.1);
    lastTime = now;
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < envelopes.length; i++) {
      envelopes[i].update(dt);
      envelopes[i].draw();
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
}());
