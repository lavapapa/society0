import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {viteSingleFile} from 'vite-plugin-singlefile';
export default defineConfig({base:'./',plugins:[react(),viteSingleFile()],build:{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:1500}});
