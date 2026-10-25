# Calibre plugins: conversion formats and ACSM tickets

This page covers what happens after you install Calibre plugins with `CWA_CALIBRE_USER_PLUGINS=true`. The install steps are in the README under [Calibre plugins (DeDRM and others)](../README.md#calibre-plugins-dedrm-and-others).

## Conversion formats in the book editor

The book editor's conversion choices come from the active input and output plugins of the configured Calibre install. A KFX input plugin, for example, makes KFX books eligible as conversion sources. A KFX output choice appears only when Calibre reports an installed output plugin that can write KFX. Plugins aren't bundled, and a separate comic or KPF workflow doesn't by itself add an `ebook-convert` output format.

The capability check follows the same `CWA_CALIBRE_USER_PLUGINS` setting as conversion jobs and refreshes at least once a minute. If the local capability probe is unavailable or invalid, the editor hides Calibre conversion choices. Directory-only OEB output is excluded, because book conversion stores one file. The separate EPUB-to-KEPUB option stays available when `kepubify` is configured.

## ACSM tickets

ACSM tickets go through Calibre's import hooks before ordinary book processing. Set up the plugin's account authorization first: installing its zip alone can't authorize Adobe fulfillment.

- With Auto-Convert off, the fulfilled EPUB or PDF is imported as returned. With it on, conversion applies to that resulting book, and a failed optional conversion keeps the fulfilled book.
- If the ACSM plugin is missing or fails, the original ticket is kept in the failed-books folder and no raw ACSM format is added to the library.
- Receipts for successful sources prevent duplicate imports and repeated fulfillment after completion.

## Recovering a failed ACSM import

Before import, the fulfilled book is stored under `processed_books/acsm_fulfilled/<ticket SHA-256>/` with its original ticket basename and a manifest that binds the ticket to the book bytes.

If import fails, the original ticket and the fulfilled book stay there. A retry uses the validated book without spending the ticket again. The recovery entry is removed only after a confirmed library import.

An interrupted or damaged entry stops automatic fulfillment and prints its path for manual recovery. Keep those files, inspect the downloaded EPUB or PDF, and import that book directly if needed. Don't delete a recovery entry just to retry a ticket that has already been consumed.
