/* Contract fixture for the cover-designer v2 e2e spec.
 *
 * These are CONTRACT shapes (state/cover-designer/v2/CONTRACT.md), served via
 * page.route — the v2 backend lives in a parallel branch, so the spec never
 * depends on which designer version the running server has. The type import
 * keeps the fixture honest: tsc fails here if the fixture drifts from the
 * contract the frontend codes against.
 */
import type { DesignerCatalogue } from '../../src/features/coverDesigner/contract';

export const CATALOGUE_V2: DesignerCatalogue = {
  styles: [
    { id: 'blocks', label: 'Blocks', description: 'Title above, authors on a colour band',
      thumbnail_url: '/cover-designer/style-thumb/blocks' },
    { id: 'banner', label: 'Banner', description: 'Title on a ribbon near the top',
      thumbnail_url: '/cover-designer/style-thumb/banner' },
    { id: 'ornamental', label: 'Ornamental', description: 'Title inside a decorative frame',
      thumbnail_url: '/cover-designer/style-thumb/ornamental' },
    { id: 'emblem', label: 'Emblem', description: 'A centred emblem above the title',
      thumbnail_url: '/cover-designer/style-thumb/emblem' },
    { id: 'stripes', label: 'Stripes', description: 'Horizontal bands behind every line',
      thumbnail_url: '/cover-designer/style-thumb/stripes' },
  ],
  schemes: [
    { id: 'ink', label: 'Ink on cream', builtin: true,
      colors: { background: '#f4efe3', band: '#1f3a5f', title: '#1f3a5f', author: '#f4efe3' } },
    { id: 'meadow', label: 'Meadow green', builtin: true,
      colors: { background: '#eef4e6', band: '#3f6b3a', title: '#24451f', author: '#f2f7ec' } },
    { id: 'ember', label: 'Ember red', builtin: true,
      colors: { background: '#fff3e6', band: '#c0392b', title: '#7a2d12', author: '#fff3e6' } },
    { id: 'slate', label: 'Slate grey', builtin: true,
      colors: { background: '#e9ecef', band: '#343a40', title: '#212529', author: '#f8f9fa' } },
    { id: 'plum', label: 'Plum violet', builtin: true,
      colors: { background: '#f3ecf7', band: '#5b2c6f', title: '#3d1e4a', author: '#f7f0fa' } },
  ],
  fonts: [
    { id: 'serif', label: 'Serif', css_stack: "Georgia, 'Times New Roman', serif",
      sample_url: '/cover-designer/font-sample/serif' },
    { id: 'sans', label: 'Sans-serif', css_stack: 'Arial, Helvetica, sans-serif',
      sample_url: '/cover-designer/font-sample/sans' },
    // No sample_url on purpose: the card must fall back to the css_stack rendering.
    { id: 'mono', label: 'Monospace', css_stack: "'Courier New', monospace", sample_url: '' },
  ],
  presets: [
    { id: 'classic', name: 'Classic', builtin: true, scope: 'library',
      design: { style: 'blocks', scheme: 'ink' } },
    { id: 'meadow', name: 'Meadow', builtin: true, scope: 'library',
      design: { style: 'banner', scheme: 'meadow' } },
    { id: 'ember', name: 'Ember', builtin: true, scope: 'library',
      design: { style: 'blocks', scheme: 'ember' } },
    { id: 'noir', name: 'Noir', builtin: false, scope: 'library',
      design: { style: 'ornamental', scheme: 'slate' } },
    { id: 'my-draft', name: 'My Draft', builtin: false, scope: 'user',
      design: { style: 'banner', scheme: 'plum' } },
  ],
  defaults: {
    style: 'blocks',
    scheme: 'ink',
    colors: {},
    fonts: {
      title: { family: 'serif', size: 64 },
      subtitle: { family: 'serif', size: 32 },
      author: { family: 'serif', size: 28 },
    },
    align: { title: 'center', subtitle: 'center', author: 'center' },
    text: { title: '{title}', subtitle: '{series} {series_index}', author: '{authors}' },
    size: { width: 1200, height: 1800 },
  },
  limits: {
    min_width: 200, max_width: 2400,
    min_height: 200, max_height: 2400,
    font_size_min: 8, font_size_max: 200,
  },
};
