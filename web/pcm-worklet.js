/* No resampling in the browser: the server receives the REAL AudioContext rate.
 * Explicit PCM16 little endian, bounded 2048-sample packets and flush handshake. */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super(); this.data = new Float32Array(2048); this.used = 0; this.active = true;
    this.port.onmessage = ({data}) => {
      if (data === "flush") { this.active = false; this.send(); this.port.postMessage({type: "flushed"}); }
    };
  }
  send() {
    if (!this.used) return;
    const buffer = new ArrayBuffer(this.used * 2), view = new DataView(buffer);
    let sum = 0;
    for (let i = 0; i < this.used; i++) {
      const v = Math.max(-1, Math.min(1, this.data[i])); sum += v * v;
      view.setInt16(i * 2, Math.round(v * (v < 0 ? 32768 : 32767)), true);
    }
    this.port.postMessage({type: "audio", buffer, rms: Math.sqrt(sum / this.used)}, [buffer]); this.used = 0;
  }
  process(inputs, outputs) {
    for (const channel of outputs[0] || []) channel.fill(0);
    if (!this.active || !inputs[0]?.length) return true;
    const channels = inputs[0], length = channels[0].length;
    for (let i = 0; i < length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      this.data[this.used++] = value / channels.length;
      if (this.used === this.data.length) this.send();
    }
    return true;
  }
}
registerProcessor("pcm-capture", PCMProcessor);
