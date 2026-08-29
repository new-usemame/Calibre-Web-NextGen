import { registerHooks } from 'node:module';

const FRONTEND_SRC_URL = new URL('../../src/', import.meta.url).href;
const TYPESCRIPT_CANDIDATE_SUFFIXES = ['.ts', '.tsx', '/index.ts', '/index.tsx'];

function isRelativeSpecifier(specifier) {
  return specifier.startsWith('./') || specifier.startsWith('../');
}

function isUnresolvedRelativeImport(error) {
  return error?.code === 'ERR_MODULE_NOT_FOUND'
    || error?.code === 'ERR_UNSUPPORTED_DIR_IMPORT';
}

function resolve(specifier, context, nextResolve) {
  if (!context.parentURL || !isRelativeSpecifier(specifier)) {
    return nextResolve(specifier, context);
  }

  try {
    return nextResolve(specifier, context);
  } catch (originalError) {
    if (!isUnresolvedRelativeImport(originalError)) throw originalError;

    for (const suffix of TYPESCRIPT_CANDIDATE_SUFFIXES) {
      const candidate = new URL(specifier + suffix, context.parentURL);
      if (!candidate.href.startsWith(FRONTEND_SRC_URL)) continue;

      try {
        return nextResolve(candidate.href, context);
      } catch (candidateError) {
        if (!isUnresolvedRelativeImport(candidateError)) throw candidateError;
      }
    }

    throw originalError;
  }
}

registerHooks({ resolve });
