/** Точка входа приложения.
 *
 *  Разметка вызывает функции через data-атрибуты: data-act="createOffer".
 *  Один делегированный слушатель в actions.js находит имя в window и зовёт
 *  функцию — поэтому модульные функции публикуются в window одним блоком ниже.
 *  Inline-обработчиков (onclick="...") в разметке больше нет: именно они
 *  заставляли держать в CSP разрешение 'unsafe-inline', с которым политика
 *  почти не защищала от XSS.
 */
import {
  initUi,
  toast,
  openModal,
  closeModal,
  confirmAccept,
  toggleDropdown,
  clearNotifs,
  openDrawer,
  closeDrawer,
} from './ui.js?v=tal32';
import { initTheme } from './theme.js?v=zephyr3';
import { initPolish } from './polish.js?v=phone3';
import { initNav, goTo } from './nav.js?v=zephyr4';
import { initAuth, enterApp, logout } from './auth.js?v=viewas1';
import {
  animateCounters,
  buildChart,
  initDashboardDynamics,
  loadOfferStats,
  setChartDays,
  showAllNotifications,
} from './dashboard.js?v=tal32';
import {
  initDatabases,
  loadDatabases,
  searchDatabases,
  deleteDatabase,
  openDatabase,
  backToDatabases,
  loadLeads,
  leadPage,
  sortLeads,
  filterLeads,
  exportLeadsCSV,
  resolveLeadNames,
  openLeadNote,
  saveLeadNote,
} from './databases.js?v=note1';
import {
  initOffers,
  loadOffers,
  createOffer,
  createOfferFromPage,
  clearOfferForm,
  previewOfferImage,
  openOfferEdit,
  removeOffer,
  resetOfferModal,
  previewOffer,
  insertPlaceholder,
  clearOfferLog,
  generateAiOffer,
  saveAiOfferSettings,
} from './offers.js?v=aioffer1';
import {
  initCampaigns,
  loadCampaigns,
  loadCampaignBases,
  launchCampaign,
  loadCampaignAccounts,
  refreshAiAvailability,
  toggleCampaignAi,
  toggleCampaignSpintax,
  clearCampaignForm,
  removeCampaign,
  clearCampaignLog,
  previewCampaign,
  hideCampaignPreview,
  sendTestMessage,
  stopAllCampaigns,
} from './campaigns.js?v=zephyr3';
import {
  initChat,
  openChatDialog,
  openChatLead,
  closeChatRoom,
  sendChatMessage,
  loadMoreDialogs,
  loadOlderMessages,
  reloadChat,
  markAllChatsRead,
  replyToChatMessage,
  cancelChatReply,
  scrollChatToBottom,
  toggleChatAccounts,
  switchChatAccount,
  openChatHeadMenu,
  toggleChatPin,
  toggleChatMute,
  markChatDialogRead,
} from './chat.js?v=tal34';
import { initSidebar, toggleSidebar } from './sidebar.js?v=tal33';
import {
  initAccountMenu,
  toggleAccountMenu,
  closeAccountMenu,
  accountGoTo,
  setAccountTheme,
} from './account.js?v=talaria14';
import { initSupport, openSupport, sendSupportMessage } from './support.js?v=talaria14';
import {
  openHelpCenter,
  openHelpForCurrentSection,
  helpToSupport,
  filterHelp,
} from './help.js?v=talaria14';
import { sendAi } from './ai.js?v=talaria13';
import { initOnboarding, openKeyGuide, startOnboarding } from './onboarding.js?v=zephyr4';
import { initActions, clearParseLogs } from './actions.js';
import {
  toggleReplyNotify,
  toggleSendWindow,
  saveSendWindow,
  saveNotifyTarget,
  testNotifyTarget,
  revokeSession,
  revokeOtherSessions,
  initSettings,
  addKey,
  addWarmupAccount,
  connectTgAccount,
  finishTgConnect,
  cancelTgConnect,
} from './settings.js?v=sess1';
import {
  initAdmin,
  loadAdmin,
  adminPage,
  adminScope,
  adminToggleRegistration,
  adminActivatePlan,
  adminToggleBlock,
  addWhitelist,
  loadLoginAttempts,
  exportLoginAttempts,
  exportActivity,
  recomputeLeadScores,
  sendAdminNotification,
  deletePayment,
  adminRevokePlan,
  setChartPeriod,
  openLoginJournal,
  openActivityJournal,
  createPromoCode,
  togglePromoCode,
  removePromoCode,
  grantDaysToAll,
  adminViewAs,
  exitViewAs,
  loadSupportThreads,
  openSupportThread,
  replySupportThread,
} from './admin.js?v=talaria14';
import {
  handlePlanAction,
  initPlans,
  loadSubscription,
  loadPublicPlans,
} from './plans.js?v=phone3';
import {
  initSearch,
  log,
  switchSearchTab,
  searchClamp,
  searchInc,
  searchDec,
  searchSelectAll,
  generateTrends,
  runTrendsSearch,
  generateNicheKeywords,
  addNicheExtraKw,
  runNicheSearch,
  addCustomKeyword,
  removeCustomKeyword,
  runCustomSearch,
  resetLeadFilters,
  setLeadFilterMode,
  runTelegramVerification,
  cancelTelegramVerification,
} from './search.js?v=talaria13';

/* Модалка оффера должна сбрасывать режим редактирования при открытии. */
function openModalWithReset(id) {
  resetOfferModal(id);
  openModal(id);
}

Object.assign(window, {
  // UI
  // Переписка
  initChat,
  openChatDialog,
  openChatLead,
  closeChatRoom,
  sendChatMessage,
  loadMoreDialogs,
  loadOlderMessages,
  reloadChat,
  markAllChatsRead,
  replyToChatMessage,
  cancelChatReply,
  scrollChatToBottom,
  toggleChatAccounts,
  switchChatAccount,
  openChatHeadMenu,
  toggleChatPin,
  toggleChatMute,
  markChatDialogRead,
  toggleSidebar,
  // Меню аккаунта, помощь и поддержка (v40)
  toggleAccountMenu,
  closeAccountMenu,
  accountGoTo,
  setAccountTheme,
  openHelpCenter,
  openHelpForCurrentSection,
  helpToSupport,
  filterHelp,
  openSupport,
  sendSupportMessage,
  toast,
  openModal: openModalWithReset,
  closeModal,
  confirmAccept,
  toggleDropdown,
  clearNotifs,
  openDrawer,
  closeDrawer,
  // Навигация
  goTo,
  // Авторизация и онбординг
  enterApp,
  logout,
  startOnboarding,
  openKeyGuide,
  // Главная
  animateCounters,
  buildChart,
  loadOfferStats,
  setChartDays,
  showAllNotifications,
  // Базы лидов
  loadDatabases,
  searchDatabases,
  deleteDatabase,
  openDatabase,
  backToDatabases,
  loadLeads,
  leadPage,
  sortLeads,
  filterLeads,
  exportLeadsCSV,
  resolveLeadNames,
  openLeadNote,
  saveLeadNote,
  // Офферы
  createOffer,
  createOfferFromPage,
  clearOfferForm,
  previewOfferImage,
  openOfferEdit,
  removeOffer,
  loadOffers,
  previewOffer,
  insertPlaceholder,
  clearOfferLog,
  generateAiOffer,
  saveAiOfferSettings,
  // Рассылки
  launchCampaign,
  previewCampaign,
  hideCampaignPreview,
  toggleCampaignAi,
  toggleCampaignSpintax,
  clearCampaignForm,
  loadCampaigns,
  loadCampaignBases,
  loadCampaignAccounts,
  refreshAiAvailability,
  removeCampaign,
  clearCampaignLog,
  // Прочее из разметки
  clearParseLogs,
  // Тестовая отправка
  sendTestMessage,
  stopAllCampaigns,
  // Тарифы и подписка
  handlePlanAction,
  loadPublicPlans,
  loadSubscription,
  // Админ
  loadAdmin,
  adminPage,
  adminScope,
  adminToggleRegistration,
  adminActivatePlan,
  adminToggleBlock,
  addWhitelist,
  loadLoginAttempts,
  exportLoginAttempts,
  exportActivity,
  recomputeLeadScores,
  sendAdminNotification,
  deletePayment,
  adminRevokePlan,
  openLoginJournal,
  openActivityJournal,
  createPromoCode,
  togglePromoCode,
  removePromoCode,
  grantDaysToAll,
  adminViewAs,
  exitViewAs,
  // Поддержка в админке (v40)
  loadSupportThreads,
  openSupportThread,
  replySupportThread,
  // Настройки — ключи YouTube / Telegram-аккаунты / прогрев
  addKey,
  addWarmupAccount,
  toggleReplyNotify,
  toggleSendWindow,
  saveSendWindow,
  saveNotifyTarget,
  testNotifyTarget,
  revokeSession,
  revokeOtherSessions,
  // Подключение Telegram — теперь живёт в настройках
  connectTgAccount,
  finishTgConnect,
  cancelTgConnect,
  // Поиск
  log,
  switchSearchTab,
  searchClamp,
  searchInc,
  searchDec,
  searchSelectAll,
  generateTrends,
  runTrendsSearch,
  generateNicheKeywords,
  addNicheExtraKw,
  runNicheSearch,
  addCustomKeyword,
  removeCustomKeyword,
  runCustomSearch,
  resetLeadFilters,
  setLeadFilterMode,
  runTelegramVerification,
  cancelTelegramVerification,
  // ИИ (реальный чат через /api/ai/chat)
  sendAi,
});

/* Загрузка данных после входа живёт в auth.js (enterApp): она нужна ровно
   в момент входа, и держать её в двух местах — верный способ разойтись. */

function boot() {
  // Делегирование действий разметки включаем первым: до него ни одна кнопка
  // с data-act не работает.
  initActions();
  initTheme();
  initDashboardDynamics();
  initPolish();
  initUi();
  initSidebar();
  initAccountMenu();
  initSupport();
  initNav();
  initSettings();
  initAdmin();
  initPlans();
  initDatabases();
  initOffers();
  initCampaigns();
  initAuth();
  initSearch();
  initOnboarding();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
