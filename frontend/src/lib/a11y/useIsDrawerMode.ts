/* Tracks whether the sidebar/top-bar are in off-canvas drawer mode.
 *
 * Drawer mode is broader than a narrow viewport: coarse-pointer and no-hover
 * devices use the drawer at tablet widths too. Focus management must follow
 * the same media state as the CSS or the off-screen drawer remains tabbable.
 *
 * The query's JavaScript source of truth lives in drawerMode.ts. CSS media rules
 * cannot import TypeScript, so structuralPerformance.test pins them to it.
 */
import { useMediaQuery } from '../useMediaQuery';
import { DRAWER_MODE_QUERY } from './drawerMode';

export { DRAWER_MODE_QUERY } from './drawerMode';

export function useIsDrawerMode(): boolean {
  return useMediaQuery(DRAWER_MODE_QUERY);
}
