import type { StyleProp, ViewStyle } from 'react-native';

export type ARStatusEvent = {
  status: string;
  message?: string;
};

export type ARPointSelectedEvent = {
  // ARCore 3D world coordinates (world-locked anchor position)
  x: number;
  y: number;
  z: number;

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

export type AROverlayUpdateEvent = {
  trackingQuality: string;
  candidateValid: boolean;
  candidateScreenX: number;
  candidateScreenY: number;
  labels: AROverlayItem[];

  /** False while the accelerometer reports the phone is in motion. */
  steady: boolean;

  /** Smoothed linear-acceleration magnitude in m/s2. */
  motionMagnitude: number;

  /** Magnitude above which the phone is declared to be moving. */
  movingThreshold: number;

  /** False when the device has no usable accelerometer (gate fails open). */
  motionSensorAvailable: boolean;
};

/**
 * Emitted only on STEADY <-> MOVING transitions, so it can drive a modal
 * without the churn of a per-frame event.
 */
export type ARSteadyChangeEvent = {
  steady: boolean;
  state: 'STEADY' | 'MOVING';

  /** Smoothed magnitude at the moment of the transition (m/s2). */
  magnitude: number;

  /** Highest magnitude reached during the motion episode (m/s2). */
  peakMagnitude: number;

  movingThreshold: number;
  steadyThreshold: number;

  /** How many points were already locked when motion interrupted. */
  pointsPlaced: number;

  sensorAvailable: boolean;
};

export type GraniteARViewProps = {
  style?: StyleProp<ViewStyle>;
  tapToken?: number;
  resetToken?: number;

  /**
   * Motion-gate tuning: [movingThreshold, steadyThreshold, steadyHoldMs].
   * Omit to use the native defaults (0.55 m/s2, 0.28 m/s2, 650 ms).
   */
  motionSensitivity?: number[];

  onStatus?: (event: {
    nativeEvent: ARStatusEvent;
  }) => void;

  onPointSelected?: (event: {
    nativeEvent: ARPointSelectedEvent;
  }) => void;

  onOverlayUpdate?: (event: {
    nativeEvent: AROverlayUpdateEvent;
  }) => void;

  onSteadyChange?: (event: {
    nativeEvent: ARSteadyChangeEvent;
  }) => void;
};
