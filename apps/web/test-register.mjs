import * as nodeModule from "node:module";

if (typeof nodeModule.registerHooks === "function") {
  nodeModule.registerHooks({
    resolve(specifier, context, nextResolve) {
      try {
        return nextResolve(specifier, context);
      } catch (error) {
        const isRelative = specifier.startsWith("./") || specifier.startsWith("../");
        const hasExtension = /\.[a-z0-9]+$/i.test(specifier);
        if (!isRelative || hasExtension || error?.code !== "ERR_MODULE_NOT_FOUND") throw error;
        return nextResolve(`${specifier}.ts`, context);
      }
    },
  });
} else {
  // Compatibility with the earliest Node 22 releases allowed by engines.
  nodeModule.register("./test-loader.mjs", import.meta.url);
}
