/**
 * The brand mark as data, so the component and the favicon draw the same glyph.
 *
 * Held here rather than in `logo.tsx` because the favicon is not a React
 * component — it is a string of SVG built at runtime — and a second copy of a
 * 900-character path is a second copy that will not be updated.
 *
 * The gradient stops are the delivered pink. The second accent is reached by
 * running `--pa-logo-filter` over them, which is what `applyCssFilter` is for;
 * neither this file nor the icon knows which accent is active.
 */

export const MARK_VIEW_BOX = "228 197 628 628";

/** Gradient stops, dark to light, as the mark reads them bottom-left to top-right. */
export const MARK_GRADIENT = ["#ee0083", "#ff14a4", "#ff4fc2"] as const;

export const MARK_PATH =
  "M 349.515 268.394 L 346.991 270.918 347.626 511.709 C 348.062 677.001, 348.589 752.913, 349.307 753.816 C 350.987 755.927, 355.670 758.002, 358.700 757.978 C 370.488 757.886, 383.931 742.493, 406.103 703.699 C 431.697 658.920, 451.652 639.075, 482.921 627.305 C 496.361 622.246, 499.251 621.855, 529.500 620.993 C 559.812 620.129, 572.059 618.836, 591.805 614.415 C 676.907 595.358, 732.625 535.708, 736.884 459.094 C 742.361 360.563, 673.152 284.653, 563 268.374 C 552.937 266.887, 539.039 266.625, 451.769 266.273 L 352.039 265.870 349.515 268.394 M 459 440.032 C 459 508.869, 458.790 520.566, 457.502 523.650 C 456.678 525.622, 452.253 531.570, 447.669 536.868 C 410.985 579.264, 389.184 615.722, 375.050 658.313 C 370.785 671.163, 371.597 671.485, 376.885 659.040 C 398.646 607.827, 436.350 566.075, 476.167 549.100 C 495.209 540.981, 505.705 538.900, 537.164 537.004 C 568.857 535.093, 588.102 529.010, 607.172 514.873 C 659.959 475.740, 652.453 395.529, 593.567 369.487 C 573.900 360.789, 566.873 360.013, 507.750 360.006 L 459 360 459 440.032 Z";
