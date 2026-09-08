import { NativeModule, requireNativeModule } from 'expo';

import { GraniteARModuleEvents } from './GraniteAR.types';

declare class GraniteARModule extends NativeModule<GraniteARModuleEvents> {
  PI: number;
  hello(): string;
  setValueAsync(value: string): Promise<void>;
}

// This call loads the native module object from the JSI.
export default requireNativeModule<GraniteARModule>('GraniteAR');
