import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base: the built prototype is served from any path (artifact, file://).
export default defineConfig({ base: './', plugins: [react()] })
