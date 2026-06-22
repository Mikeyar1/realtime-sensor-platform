/**
 * chart-factory.js — Chart Card Factory
 *
 * Composes a ChartCard wrapper with the correct concrete chart implementation.
 * Add a new case here each time a new chart-*.js is registered.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartFactory = (function () {

  function create(descriptor) {
    let chartInstance = null;

    const card = LabReplay.ChartCard.create(descriptor, {
      onClose() {
        if (chartInstance?.destroy) chartInstance.destroy();
      },
    });

    // Tag the body element with the card type for CSS targeting
    card.body.dataset.cardType = descriptor.cardType;

    switch (descriptor.cardType) {

      case 'hr-line':
        chartInstance = LabReplay.ChartHR.create(card.body, card, descriptor);
        break;

      case 'pupil-line':
        chartInstance = LabReplay.ChartPupil.create(card.body, card, descriptor);
        break;

      case 'gaze-scatter':
        chartInstance = LabReplay.ChartGaze.create(card.body, card, descriptor);
        break;

      case 'hrv-rmssd':
        chartInstance = LabReplay.ChartHRV.create(card.body, card, descriptor);
        break;

      case 'motion-strip':
        chartInstance = LabReplay.ChartMotion.create(card.body, card, descriptor);
        break;

      // Future cases:
      // case 'ecg-line':  chartInstance = LabReplay.ChartECG.create(...);  break;
      // case 'acc-line':  chartInstance = LabReplay.ChartACC.create(...);   break;

      default:
        console.warn(`[ChartFactory] No chart implementation for cardType: "${descriptor.cardType}"`);
        card.body.innerHTML = '<div class="chart-no-data">Chart not yet implemented</div>';
        chartInstance = { pushSample() {}, resize() {}, destroy() {} };
    }

    return { el: card.el, instance: chartInstance };
  }

  return { create };
})();
