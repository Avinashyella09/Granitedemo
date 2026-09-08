import { requireNativeView } from 'expo';
import * as React from 'react';

import { GraniteARViewProps } from './GraniteAR.types';

const NativeView: React.ComponentType<GraniteARViewProps> =
  requireNativeView('GraniteAR');

export default function GraniteARView(props: GraniteARViewProps) {
  return <NativeView {...props} />;
}
