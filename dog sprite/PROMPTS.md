# Lumon Dog Sprite Prompts

## Prompting Strategy
These prompts are optimized for:
- one animation state at a time
- tight frame-count control
- strong silhouette readability at small size
- one horizontal sprite strip
- enough whitespace around the character so frame extraction is easier
- a single flat chroma-key background so cleanup is reliable

The target character is a small, premium-feeling dog mascot for Lumon. It should read clearly at cursor-adjacent size and feel warm, competent, and slightly playful rather than goofy.

## Shared Character Lock
Use this idea in every prompt:

- tiny shiba/corgi-like dog mascot
- warm tan and cream palette
- readable ears, face, paws, and body silhouette
- side-scroller camera
- 2D pixel-art game sprite
- one horizontal row of evenly spaced frames
- a single flat uniform bright lime or green-screen background
- no props
- no text
- no UI
- no extra characters
- consistent scale and baseline in every frame
- no floor, no shadow, no platform, no paper texture, no checkerboard

## Reference Prompt
```text
Create a polished 2D pixel-art sprite sheet for a tiny shiba/corgi-like dog mascot used as a browser-agent overlay in a developer product. The dog should have a warm tan-and-cream palette, a compact body, readable ears, a simple face, and a clean silhouette that stays recognizable at very small size.

Style requirements:
- crisp game-ready pixel art
- side-scroller view
- one horizontal row of frames
- evenly spaced frames
- consistent baseline and scale across all frames
- a single flat uniform bright lime green chroma-key background
- no text
- no props
- no environment
- no duplicate limbs
- no extra characters
- smooth frame-to-frame consistency
- no floor, no shadow, no platform, no paper texture, no checkerboard, no vignette

The sprite should feel premium, calm, trustworthy, and slightly playful, not chaotic or exaggerated.
```

## Idle Prompt
```text
Create a polished 2D pixel-art sprite sheet for the exact same tiny shiba/corgi-like dog mascot in a subtle idle loop. Use a side-scroller view and a warm tan-and-cream palette. The dog should feel alert, calm, and alive.

Requirements:
- 6 animation frames
- one horizontal row
- evenly spaced frames
- consistent scale and baseline
- a single flat uniform bright lime green chroma-key background
- no text, no props, no environment
- crisp game-ready pixel art

Animation behavior:
- tiny breathing motion
- small ear movement
- soft body bob
- restrained motion that loops cleanly

Important:
- this must look like a real runtime sprite sheet, not concept art
- keep the character centered with enough empty space around it for frame extraction
- do not change the character design between frames
- keep the dog facing left in the same side-view silhouette in every frame
- absolutely no ground line, floor strip, shadow plate, or decorative background texture
```

## Busy Prompt
```text
Create a polished 2D pixel-art sprite sheet for the exact same tiny shiba/corgi-like dog mascot in a focused busy loop. Use a side-scroller view and a warm tan-and-cream palette.

Requirements:
- 6 animation frames
- one horizontal row
- evenly spaced frames
- consistent scale and baseline
- a single flat uniform bright lime green chroma-key background
- no text, no props, no environment
- crisp game-ready pixel art

Animation behavior:
- compact focused motion
- slightly quicker rhythm than idle
- subtle head and ear tension
- small paw/body adjustments suggesting active thinking or working

Important:
- this is not locomotion
- avoid exaggerated comedy motion
- keep the silhouette clean and consistent
- absolutely no ground line, floor strip, shadow plate, or decorative background texture
```

## Success Prompt
```text
Create a polished 2D pixel-art sprite sheet for the exact same tiny shiba/corgi-like dog mascot performing a short success emote. Use a side-scroller view and a warm tan-and-cream palette.

Requirements:
- 6 animation frames
- one horizontal row
- evenly spaced frames
- consistent scale and baseline
- a single flat uniform bright lime green chroma-key background
- no text, no props, no environment
- crisp game-ready pixel art

Animation behavior:
- one short positive bounce
- confident happy posture
- very small tail or ear accent only if it stays readable
- clean one-shot celebratory energy

Important:
- premium and restrained, not a silly dance
- keep the dog readable at tiny size
- preserve character identity exactly
- keep the dog facing left in the same side-view silhouette in every frame
- do not turn toward the camera
- do not switch to a front-facing pose
- keep the dog on all four legs in every frame except for a tiny controlled hop
- do not make the dog stand upright like a human
- absolutely no ground line, floor strip, shadow plate, or decorative background texture
```

## Error Prompt
```text
Create a polished 2D pixel-art sprite sheet for the exact same tiny shiba/corgi-like dog mascot performing a short error or failure emote. Use a side-scroller view and a warm tan-and-cream palette.

Requirements:
- 6 animation frames
- one horizontal row
- evenly spaced frames
- consistent scale and baseline
- a single flat uniform bright lime green chroma-key background
- no text, no props, no environment
- crisp game-ready pixel art

Animation behavior:
- small recoil or slump
- slight tense posture
- controlled frustrated or confused reaction
- readable but restrained one-shot motion

Important:
- avoid chaotic shaking
- keep the silhouette stable and clean
- maintain a premium product feel
- keep the dog facing left in the same side-view silhouette in every frame
- do not switch breed, ear shape, tail shape, or body proportions
- do not use front-facing or 3/4 camera angles
- absolutely no ground line, floor strip, shadow plate, or decorative background texture
```

## Locomotion Prompt
```text
Create a polished 2D pixel-art sprite sheet for the exact same tiny shiba/corgi-like dog mascot in a true locomotion cycle. Use a side-scroller view and a warm tan-and-cream palette.

Requirements:
- 8 animation frames
- one horizontal row
- evenly spaced frames
- consistent scale and baseline
- a single flat uniform bright lime green chroma-key background
- no text, no props, no environment
- crisp game-ready pixel art

Animation behavior:
- smooth purposeful movement
- compact stride suitable for a tiny UI mascot
- no exaggerated stretching
- premium and controlled, not cartoon-chaotic

Important:
- this must be a true movement cycle, not idle reused as movement
- preserve exact character identity across all frames
- leave enough empty space around the character so each frame can be extracted cleanly
- absolutely no ground line, floor strip, shadow plate, or decorative background texture
```
