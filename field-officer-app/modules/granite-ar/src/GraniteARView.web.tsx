import * as React from 'react';

import { GraniteARViewProps } from './GraniteAR.types';

export default function GraniteARView(props: GraniteARViewProps) {
  return (
    <div>
      <iframe
        style={{ flex: 1 }}
        src={props.url}
        onLoad={() => props.onLoad({ nativeEvent: { url: props.url } })}
      />
    </div>
  );
}
