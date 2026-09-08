import { registerWebModule, NativeModule } from 'expo';

import { GraniteARModuleEvents } from './GraniteAR.types';

class GraniteARModule extends NativeModule<GraniteARModuleEvents> {
  PI = Math.PI;
  async setValueAsync(value: string): Promise<void> {
    this.emit('onChange', { value });
  }
  hello() {
    return 'Hello world! 👋';
  }
}

export default registerWebModule(GraniteARModule, 'GraniteARModule');
