/**
 * CSS Modules resolve to a class-name map at build time; Vite supplies the
 * runtime. This tells TypeScript what shape to expect.
 */
declare module "*.module.css" {
  const classes: Readonly<Record<string, string>>;
  export default classes;
}
