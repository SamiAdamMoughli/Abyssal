import { defineConfig } from 'vite';

// On Linux, Docker publishes ports to the bridge gateway (172.17.0.1), not
// to 127.0.0.1. DOCKER_HOST_IP overrides this for other environments.
const API_TARGET = process.env.DOCKER_HOST_IP
  ? `http://${process.env.DOCKER_HOST_IP}:8000`
  : 'http://172.17.0.1:8000';

export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
});
