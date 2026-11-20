/*
 * Optional desktop audio output for this noVNC page.
 * Audio is carried over its own WebSocket and never shares the VNC data stream.
 */
const AudioOutput = {
    socket: null,
    context: null,
    nextPlayTime: 0,
    button: null,

    init() {
        if (!window.WebSocket || !window.AudioContext) return;

        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Enable audio';
        button.title = 'Play the remote desktop audio';
        button.setAttribute('aria-label', 'Enable remote desktop audio');
        button.style.cssText = [
            'position:fixed',
            'right:16px',
            'bottom:16px',
            'z-index:10000',
            'padding:10px 15px',
            'border:1px solid #777',
            'border-radius:7px',
            'background:#24232e',
            'color:#fff',
            'font:14px sans-serif',
            'cursor:pointer',
            'box-shadow:0 2px 8px rgba(0,0,0,.35)'
        ].join(';');

        button.addEventListener('click', () => {
            if (this.socket || this.context) {
                this.stop();
            } else {
                this.start();
            }
        });

        document.body.appendChild(button);
        this.button = button;
    },

    async start() {
        if (!this.button) return;
        this.button.disabled = true;
        this.button.textContent = 'Starting audio…';

        try {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            this.context = new AudioContextClass({ sampleRate: 48000 });
            await this.context.resume();

            const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(scheme + '//' + window.location.host + '/audio');
            this.socket = socket;
            socket.binaryType = 'arraybuffer';

            socket.addEventListener('open', () => {
                if (this.socket !== socket) return;
                this.button.disabled = false;
                this.button.textContent = 'Audio on · Stop';
            });

            socket.addEventListener('message', (event) => {
                if (this.socket === socket && event.data instanceof ArrayBuffer) {
                    this.playPcm(event.data);
                }
            });

            socket.addEventListener('error', () => {
                if (this.socket === socket) {
                    this.button.disabled = false;
                    this.button.textContent = 'Audio unavailable · Retry';
                }
            });

            socket.addEventListener('close', () => {
                if (this.socket === socket) {
                    this.socket = null;
                    this.closeContext();
                    this.button.disabled = false;
                    this.button.textContent = 'Enable audio';
                }
            });
        } catch (error) {
            this.stop();
            if (this.button) {
                this.button.disabled = false;
                this.button.textContent = 'Audio unavailable · Retry';
            }
        }
    },

    playPcm(data) {
        if (!this.context || data.byteLength < 4) return;

        const sampleCount = Math.floor(data.byteLength / 2);
        const frameCount = Math.floor(sampleCount / 2);
        if (frameCount < 1) return;

        const view = new DataView(data);
        const buffer = this.context.createBuffer(2, frameCount, 48000);
        const left = buffer.getChannelData(0);
        const right = buffer.getChannelData(1);

        for (let i = 0; i < frameCount; i++) {
            left[i] = view.getInt16(i * 4, true) / 32768;
            right[i] = view.getInt16(i * 4 + 2, true) / 32768;
        }

        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);

        const now = this.context.currentTime;
        if (this.nextPlayTime < now + 0.08) {
            this.nextPlayTime = now + 0.08;
        }
        source.start(this.nextPlayTime);
        this.nextPlayTime += buffer.duration;
    },

    closeContext() {
        if (this.context) {
            const context = this.context;
            this.context = null;
            context.close().catch(() => {});
        }
        this.nextPlayTime = 0;
    },

    stop() {
        const socket = this.socket;
        this.socket = null;
        if (socket) {
            try { socket.close(); } catch (_) {}
        }
        this.closeContext();
        if (this.button) {
            this.button.disabled = false;
            this.button.textContent = 'Enable audio';
        }
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => AudioOutput.init(), { once: true });
} else {
    AudioOutput.init();
}
