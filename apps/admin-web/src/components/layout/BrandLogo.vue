<!-- The Ghidorah brand of the top bar as the Ledger design draws it: the mark (24 px, brand purple) and the word
     "Ghidorah" in Manrope 800 at 16 px, 10 px apart, in both themes. On a tablet and a phone (JobsTablet, JobsPhone)
     the bar shows the mark alone before the page title. The home link around it carries the name; the mark is
     decoration. Centred in the flex row, the mark's drawing sits 0.3 px from the optical middle of the word (its ink
     box), which the browser test measures. -->
<template>
  <span class="brand" data-testid="brand">
    <svg class="brand__mark" viewBox="50 20 510 520" width="24" height="24" aria-hidden="true">
      <g>
        <path
          d="M369 64L353 24L406 49Q437 61 453 81L500 104L490 131L447 123L406 125Q391 125 382 113C247 98 123 169 104 287C98 327 108 364 128 390C61 361 49 296 67 233C105 110 239 52 369 64Z"
        />
        <path
          d="M392 204L378 173L431 198Q459 209 475 230L519 251L509 278L470 272L430 274Q414 274 404 263H294C219 263 175 295 172 341C167 394 213 432 275 432C309 432 341 421 368 402L346 386H301L264 346H391L377 316L435 343Q464 354 479 375L548 407L538 434L500 431L471 436C428 492 366 523 286 523C174 523 96 458 96 366C96 268 176 204 287 204Z"
        />
      </g>
    </svg>
    <span class="brand__word">Ghidorah</span>
  </span>
</template>

<style scoped>
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--rl-fg);
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.02em;
  white-space: nowrap;
}
.brand__mark {
  display: block;
  flex: none;
  fill: var(--rl-brand);
}
@media (max-width: 1279px) {
  .brand__word {
    display: none;
  }
}
</style>
