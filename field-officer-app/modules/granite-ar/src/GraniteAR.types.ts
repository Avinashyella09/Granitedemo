import type { StyleProp, ViewStyle } from 'react-native';

export type ARStatusEvent = {
  status: string;
  message?: string;
};

export type ARPointSelectedEvent = {
  /** 1-based position of this point in the placement chain. */
  index: number;
  /** How many points exist natively after this placement. */
  count: number;

  /** ARKit/ARCore world coordinates of the anchor created for this point. */
  x: number;
  y: number;
  z: number;

  /** Which raycast source produced the hit: mesh | existingPlane | estimatedPlane. */
  trackable: string;
};

export type AROverlayItem = {
  id: string;
  text: string;
  distance: number;
  screenX: number;
  screenY: number;
  visible: boolean;
};

/** Edge lengths, in metres, derived from the placed chain. Null until all four points exist. */
export type ARMeasurement = {
  /** point 1 -> point 2 */
  length: number;
  /** point 2 -> point 3 */
  height: number;
  /** point 3 -> point 4 */
  breadth: number;
  volume: number;
};

export type AROverlayUpdateEvent = {
  trackingQuality: string;
  /** A tap right now would be accepted. */
  candidateValid: boolean;
  candidateScreenX: number;
  candidateScreenY: number;
  labels: AROverlayItem[];

  /**
   * The native layer is the single source of truth for the placed points: JS must mirror
   * `points`/`pointCount` rather than maintaining its own array, or the two counts drift apart and
   * the UI ends up asking for a different point than the one native is waiting on.
   */
  pointCount?: number;
  points?: number[][];
  measurement?: ARMeasurement | null;

  /** Non-blocking observation about the placed chain (e.g. two edges running the same way). */
  advisory?: string;

  /** A settled surface reading exists, independent of whether there is room for another point. */
  candidateStable?: boolean;

  /**
   * Dev-diagnostics payload (reticle/viewport geometry, candidate world coordinates, capability
   * flags, anchor refinement). Shape intentionally loose since it is HUD-only and never relied on
   * for measurement logic.
   */
  diagnostics?: Record<string, unknown>;
};

export type GraniteARViewProps = {
  style?: StyleProp<ViewStyle>;
  /** Increment to place a point at the current reticle. */
  tapToken?: number;
  /** Increment to clear every placed point. */
  resetToken?: number;
  /** Increment to remove the most recently placed point. */
  undoToken?: number;

  onStatus?: (event: {
    nativeEvent: ARStatusEvent;
  }) => void;

  onPointSelected?: (event: {
    nativeEvent: ARPointSelectedEvent;
  }) => void;

  onOverlayUpdate?: (event: {
    nativeEvent: AROverlayUpdateEvent;
  }) => void;
};
