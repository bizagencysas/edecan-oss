// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Projects module — persistent master HTML workspace system                   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export {
  createProject,
  findOldestProjectByBrandId,
  findProjectById,
  findProjectBySlug,
  resolveProjectByReference,
  listProjects,
  updateProject,
  addVariant,
  getVariants,
  getVariantHtml,
  updateVariant,
  deleteVariant,
  getExistingFolders,
  getProjectVariantLabels,
  searchVariants,
} from './project-store';

export type { DesignProjectRow, DesignVariantRow, VariantSearchResult } from './project-store';

export {
  buildMasterHtml,
  regenerateMasterHtml,
  ensureProjectDirs,
  getMasterHtmlPath,
  getProjectDir,
} from './master-html-builder';

export type { MasterHtmlConfig } from './master-html-builder';

export { generateSlug, slugMatchScore, normalizeForSearch } from './slug-resolver';
export { generateProjectName } from './name-generator';
