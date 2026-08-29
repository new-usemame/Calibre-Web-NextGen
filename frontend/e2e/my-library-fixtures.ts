/** Compatibility import retained for my-library.spec.ts. The implementation is
 * single-sourced so its teardown and killed-run recovery cannot drift. */
export { test, expect, type SecondaryUserSession } from './fixtures';
