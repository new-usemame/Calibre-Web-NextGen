## Overview

Calibre supports subgroups via custom columns. ref: https://manual.calibre-ebook.com/sub_groups.html
This PR adds support for browsing Calibre custom columns containing hierarchical values using Calibre's `.` separator convention.

The change introduces:

- Generic hierarchical custom-column detection
- Hierarchy parsing and tree construction
- Detection of actual hierarchical value sets rather than assuming every dotted value is hierarchical
- Hierarchical descendant filtering
- Hierarchical search semantics
- Tree-based browsing in the web UI
- Breadcrumb navigation
- Per-user sidebar visibility for custom columns
- OPDS navigation for hierarchical custom columns
- Expand/collapse state persistence in the browser
- Support for arbitrary text/enumeration custom columns