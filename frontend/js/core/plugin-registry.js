/**
 * plugin-registry.js
 * 
 * Central registry where chart plugins register themselves.
 * The system queries the registry to find the right plugin for a stream type.
 *
 * Usage from a plugin file:
 *   LabReview.registerPlugin({ id, name, streamTypes, create });
 *
 * Usage from core code:
 *   const plugin = LabReview.getPluginFor("continuous");
 *   const instance = LabReview.createChart("waveform", container, meta);
 */

window.LabReview = window.LabReview || {};

// The registry — a dictionary of plugin descriptors keyed by id
LabReview._plugins = {};

/**
 * Register a chart plugin.
 * @param {Object} descriptor
 * @param {string}   descriptor.id          - Unique plugin identifier
 * @param {string}   descriptor.name        - Human-readable name
 * @param {string[]} descriptor.streamTypes - Stream types this plugin handles
 * @param {Function} descriptor.create      - Factory: (container, streamMeta) => pluginInstance
 */
LabReview.registerPlugin = function (descriptor) {
  if (!descriptor.id || !descriptor.create) {
    console.error('[PluginRegistry] Invalid plugin descriptor:', descriptor);
    return;
  }
  LabReview._plugins[descriptor.id] = descriptor;

};

/**
 * Find the first plugin that can handle the given stream type.
 * @param {string} streamType
 * @returns {Object|undefined} plugin descriptor
 */
LabReview.getPluginFor = function (streamType) {
  return Object.values(LabReview._plugins)
    .find(p => p.streamTypes.includes(streamType));
};

/**
 * Create a chart instance using a specific plugin.
 * @param {string} pluginId
 * @param {HTMLElement} container
 * @param {Object} streamMeta - Stream metadata from the catalog
 * @returns {Object} plugin instance with pushSample, resize, destroy methods
 */
LabReview.createChart = function (pluginId, container, streamMeta) {
  const plugin = LabReview._plugins[pluginId];
  if (!plugin) {
    console.error(`[PluginRegistry] Unknown plugin: "${pluginId}"`);
    return null;
  }
  return plugin.create(container, streamMeta);
};

/**
 * List all registered plugin IDs.
 * @returns {string[]}
 */
LabReview.listPlugins = function () {
  return Object.keys(LabReview._plugins);
};
