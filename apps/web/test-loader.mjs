/** Node's native TypeScript stripping does not resolve extensionless `.ts`
 * imports. Next.js does, so the test-only hook mirrors that resolution without
 * adding a second transpiler or changing production import conventions. */
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    const isRelative = specifier.startsWith("./") || specifier.startsWith("../");
    const hasExtension = /\.[a-z0-9]+$/i.test(specifier);
    if (!isRelative || hasExtension || error?.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return nextResolve(`${specifier}.ts`, context);
  }
}
