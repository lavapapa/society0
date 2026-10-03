import { mergeConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import config from "./vite.config.mjs";

export default mergeConfig(config, {
  base: "./",
  build: {
    outDir: "dist/standalone",
    emptyOutDir: true,
  },
  plugins: [viteSingleFile()],
});
