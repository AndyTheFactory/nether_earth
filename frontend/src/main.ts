import { Application, Graphics } from 'pixi.js';
import './style.css';

async function main(): Promise<void> {
  const host = document.querySelector<HTMLDivElement>('#app');
  if (!host) {
    throw new Error('Missing #app host element');
  }

  const app = new Application();
  await app.init({
    background: '#10151d',
    resizeTo: window,
    antialias: false,
  });

  host.appendChild(app.canvas);

  const marker = new Graphics().rect(0, 0, 48, 48).fill(0xb7c5d8);
  marker.position.set(24, 24);
  app.stage.addChild(marker);
}

void main();
