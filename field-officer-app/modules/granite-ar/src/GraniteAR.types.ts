import type { StyleProp, ViewStyle } from 'react-native';

export type ARStatusEvent = {
  status: string;
  message?: string;
};

export type ARPointSelectedEvent = {
  screenX: number;
  screenY: number;

  // Android native view size in physical pixels
  viewWidth: number;
  viewHeight: number;

  // ARCore 3D world coordinates
  x: number;
  y: number;
  z: number;

  trackable: string;
};

export type GraniteARViewProps = {
  style?: StyleProp<ViewStyle>;

  onStatus?: (event: {
    nativeEvent: ARStatusEvent;
  }) => void;

  onPointSelected?: (event: {
    nativeEvent: ARPointSelectedEvent;
  }) => void;
};