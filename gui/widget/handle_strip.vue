<template>
  <div class="dah-root" :style="{ height: rail_h + 'px' }">
    <div class="dah-base"></div>
    <div v-for="(x, i) in ticks" :key="'t' + i" class="dah-tick" :style="{ left: x + '%' }"></div>
    <div v-if="both" class="dah-bracket" :style="span(13, 6)"></div>
    <div v-if="both && !moving" :class="['dah-gap', { 'dah-warn': gap_warn }]"
         :style="{ left: (pos('b') + pos('a')) / 2 + '%' }">{{ gap }}</div>
    <div v-if="both" class="dah-wash" :style="span(rail_h, lane_h)"></div>
    <div v-if="glow" class="dah-glow" :style="glow"></div>
    <div
      v-for="side in ['b', 'a']"
      :key="side"
      :class="['dah-h', side === 'b' ? 'dah-before' : 'dah-after',
               { 'dah-unset': !chosen(side), 'dah-moving': isMoving(side) }]"
      :style="{ left: pos(side) + '%' }"
      tabindex="0"
      role="slider"
      :aria-label="labels[side]"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="Math.round(pos(side))"
      :aria-valuetext="valuetext(side)"
      @pointerdown="down(side, $event)"
      @pointermove="move(side, $event)"
      @pointerup="up(side)"
      @pointercancel="cancel(side)"
      @keydown="key(side, $event)"
      @blur="blur(side)"
    >
      <span v-if="isMoving(side) && pendingRow(side)" class="dah-flag">{{ pendingRow(side).flag }}</span>
      <svg width="11" height="14" viewBox="0 0 11 14" aria-hidden="true">
        <polygon :points="side === 'b' ? '0,0 11,0 11,14' : '0,0 11,0 0,14'" />
      </svg>
    </div>
  </div>
</template>

<script>
module.exports = {
  data() {
    // free: where a dragged handle is, in % of the track, while the pointer
    // holds it. pending: the row it would land on (the drag's nearest, or the
    // row the arrow keys walked to). shown: a pick sent to the kernel and not
    // answered yet, so the handle does not jump back for one round trip.
    return { drag: null, free: { b: null, a: null }, pending: { b: null, a: null },
             shown: { b: null, a: null } };
  },
  computed: {
    both() { return !!(this.chosen('b') && this.chosen('a')); },
    moving() { return this.isMoving('b') || this.isMoving('a'); },
    glow() {
      const side = this.isMoving('b') ? 'b' : (this.isMoving('a') ? 'a' : null);
      const c = side && this.pendingRow(side);
      if (!c) return null;
      const box = c.w != null
        ? { left: c.x0 + '%', width: c.w + '%' }
        : { left: 'calc(' + c.x + '% - 6px)', width: '12px' };
      return Object.assign(box, { top: (this.rail_h + c.top) + 'px', height: c.h + 'px' });
    },
  },
  watch: {
    // the kernel answered: whatever it says is where the handle is now
    before() { this.shown.b = null; },
    after() { this.shown.a = null; },
  },
  methods: {
    chosen(side) {
      if (this.shown[side]) return this.shown[side];
      return side === 'b' ? this.before : this.after;
    },
    other(side) { return this.chosen(side === 'b' ? 'a' : 'b'); },
    isMoving(side) { return this.drag === side || this.pending[side] !== null; },
    pendingRow(side) {
      const id = this.pending[side];
      return id === null ? null : (this.candidates.find(c => c.id === id) || null);
    },
    pos(side) {
      if (this.free[side] !== null) return this.free[side];
      const p = this.pendingRow(side);
      if (p) return p.x;
      const c = this.chosen(side);
      if (c) return c.x;
      return side === 'b' ? 0 : 100;
    },
    span(top, height) {
      const x0 = this.pos('b'), x1 = this.pos('a');
      return { left: x0 + '%', width: Math.max(x1 - x0, 0) + '%', top: top + 'px', height: height + 'px' };
    },
    valuetext(side) {
      const p = this.pendingRow(side);
      if (p) return p.flag;
      const c = this.chosen(side);
      return c ? c.flag : this.labels.unset;
    },
    reach(side) {
      const o = this.other(side);
      if (!o) return this.candidates;
      return this.candidates.filter(c => (side === 'b' ? c.x < o.x : c.x > o.x));
    },
    nearest(side, x) {
      // strict "<": of two rows at one position the first wins, and the
      // kernel sends them Sentinel-2 first
      let best = null;
      for (const c of this.reach(side)) {
        if (best === null || Math.abs(c.x - x) < Math.abs(best.x - x)) best = c;
      }
      return best;
    },
    down(side, e) {
      if (e.button !== undefined && e.button !== 0) return;
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      e.currentTarget.focus();
      this.drag = side;
      this.move(side, e);
    },
    move(side, e) {
      if (this.drag !== side) return;
      const r = this.$el.getBoundingClientRect();
      let x = Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100));
      const o = this.other(side);
      if (o) x = side === 'b' ? Math.min(x, o.x - 0.2) : Math.max(x, o.x + 0.2);
      this.free[side] = x;
      const c = this.nearest(side, x);
      this.pending[side] = c ? c.id : null;
    },
    up(side) {
      if (this.drag !== side) return;
      this.drag = null;
      this.free[side] = null;
      this.commit(side);
    },
    cancel(side) {
      if (this.drag === side) this.drag = null;
      this.free[side] = null;
      this.pending[side] = null;
    },
    blur(side) { if (this.drag !== side) this.cancel(side); },
    commit(side) {
      const row = this.pendingRow(side);
      this.pending[side] = null;
      const c = this.chosen(side);
      if (!row || (c && c.id === row.id)) return;
      this.shown[side] = row;
      // a refused pick never changes the trait, so nothing would clear it
      setTimeout(() => { if (this.shown[side] === row) this.shown[side] = null; }, 3000);
      this.pick({ side: side, id: row.id });
    },
    key(side, e) {
      // Arrows walk, Enter assigns: each assignment costs the pane an Earth
      // Engine redraw, so walking ten images must not queue ten of them.
      const list = this.reach(side);
      if (!list.length) return;
      const at = this.pending[side] !== null ? this.pending[side] : (this.chosen(side) || {}).id;
      let i = list.findIndex(c => c.id === at);
      let next = null;
      const k = e.key;
      if (k === 'ArrowLeft' || k === 'Left' || k === 'ArrowDown') {
        if (i < 0) { const x = this.pos(side); const l = list.filter(c => c.x < x); next = l[l.length - 1] || list[0]; }
        else next = list[Math.max(0, i - 1)];
      } else if (k === 'ArrowRight' || k === 'Right' || k === 'ArrowUp') {
        if (i < 0) { const x = this.pos(side); next = list.find(c => c.x > x) || list[list.length - 1]; }
        else next = list[Math.min(list.length - 1, i + 1)];
      } else if (k === 'Home') next = list[0];
      else if (k === 'End') next = list[list.length - 1];
      else if (k === 'Enter') { e.preventDefault(); this.commit(side); return; }
      else if (k === 'Escape' || k === 'Esc') { this.cancel(side); return; }
      else return;
      e.preventDefault();
      this.pending[side] = next.id;
    },
  },
};
</script>
