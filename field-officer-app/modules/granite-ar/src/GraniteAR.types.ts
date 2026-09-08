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
};

export type GraniteARViewProps = {
  style?: StyleProp<ViewStyle>;
  tapToken?: number;
  resetToken?: number;

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