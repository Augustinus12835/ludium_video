
---

## The on-screen presenter (read this before anything above conflicts)

This video has an on-screen presenter who speaks every word of the narration (`scripts/presenter.py`).
The compile keys the presenter over the finished cut; `folio.py still` already shows the presenter
where the compile will put them, with a red outline of the reserved zone, so you judge frames as
viewers will see them (`--bare` leaves the presenter off). `folio.json` carries `"presenter": true`
(the director writes it; `folio.py audit` errors without it).

- **The hook = script frame 0 (the cold open, ~30 s).** The presenter stands full-figure on the
  RIGHT, in front of the picture (x ≥ 1180). For the scenes shown during the hook (`folio.py audit`
  lists any that break the rule):
  - every plate they use carries `"hook": true` — its image prompt then composes the subject and
    action in the LEFT three-fifths and continues the same scene, quiet but fully drawn, across the
    right two-fifths (sky, a wall, an empty courtyard, a field) so that the presenter stands INSIDE
    the picture, never on blank paper. Write the plate's `prompt` the same way: say what fills the
    right side;
  - camera stops keep the subject left of x ≈ 1100; overlays (Tag, Pin, Statement, Card, Kicker)
    stay at x < 1150; the stage narrows the captions to the left for the hook and hides the
    right-hand chrome (plate numeral, timecode).
- **After the hook:** a small paper window holds the **top-right corner** for the rest of the film
  (x ≥ 1496 and y ≤ 424). Nothing may sit there — kickers, plate furniture, a card's name, a map
  legend, the end of a Statement line. The plate numeral already moves left of it. Every plate (not
  only the hook's) is prompted to keep its upper-right corner calm; camera stops on a face or a
  detail keep it out of that corner (aim the `on` point at x < 1350 when y < 450).
- Do not draw the presenter, mention them, or leave a frame for them: the window and the hook
  figure are added after render.
