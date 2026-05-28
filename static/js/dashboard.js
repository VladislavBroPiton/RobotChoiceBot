// ========== МЕНЮ ==========
const menuBtn = document.getElementById('menuBtn');
const menuDropdown = document.getElementById('menuDropdown');

function toggleMenu() {
    menuDropdown.classList.toggle('show');
}

function closeMenu() {
    menuDropdown.classList.remove('show');
}

menuBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleMenu();
});

document.addEventListener('click', (e) => {
    if (!menuBtn?.contains(e.target) && !menuDropdown?.contains(e.target)) {
        closeMenu();
    }
});

// ========== ТЕМА И АКЦЕНТ ==========
function initThemeFromMenu() {
    const savedTheme = localStorage.getItem('theme');
    const savedAccent = localStorage.getItem('accent') || 'blue';
    const themeIcon = document.getElementById('menuThemeIcon');
    const themeText = document.getElementById('menuThemeText');
    
    if (savedTheme === 'dark') {
        document.body.classList.add('dark');
        themeIcon.textContent = '☀️';
        themeText.textContent = 'Светлая тема';
    } else {
        document.body.classList.remove('dark');
        themeIcon.textContent = '🌙';
        themeText.textContent = 'Тёмная тема';
    }
    
    document.body.classList.add(`accent-${savedAccent}`);
    const accentOptions = document.querySelectorAll('.accent-option');
    accentOptions.forEach(opt => {
        if (opt.dataset.accent === savedAccent) {
            opt.classList.add('active');
        } else {
            opt.classList.remove('active');
        }
    });
}

function toggleThemeFromMenu() {
    const themeIcon = document.getElementById('menuThemeIcon');
    const themeText = document.getElementById('menuThemeText');
    
    if (document.body.classList.contains('dark')) {
        document.body.classList.remove('dark');
        localStorage.setItem('theme', 'light');
        themeIcon.textContent = '🌙';
        themeText.textContent = 'Тёмная тема';
    } else {
        document.body.classList.add('dark');
        localStorage.setItem('theme', 'dark');
        themeIcon.textContent = '☀️';
        themeText.textContent = 'Светлая тема';
    }
}

function setAccent(accent) {
    document.body.classList.remove('accent-blue', 'accent-green', 'accent-neon');
    document.body.classList.add(`accent-${accent}`);
    localStorage.setItem('accent', accent);
    
    const accentOptions = document.querySelectorAll('.accent-option');
    accentOptions.forEach(opt => {
        if (opt.dataset.accent === accent) {
            opt.classList.add('active');
        } else {
            opt.classList.remove('active');
        }
    });
}

document.getElementById('themeMenuItem')?.addEventListener('click', toggleThemeFromMenu);
document.getElementById('logoutMenuItem')?.addEventListener('click', () => {
    localStorage.removeItem('crm_token');
    localStorage.removeItem('theme');
    window.location.href = '/';
});

document.querySelectorAll('.accent-option').forEach(opt => {
    opt.addEventListener('click', () => setAccent(opt.dataset.accent));
});

initThemeFromMenu();

// ========== UTM-СТАТИСТИКА ИЗ МЕНЮ ==========
document.getElementById('utmMenuItem')?.addEventListener('click', () => {
    closeMenu();
    switchTab('utm');
});

// ========== ПЕРЕМЕННЫЕ ==========
let currentChatId = null;
let currentAutoMode = true;
let currentStatusFilter = 'all';
let filterPanelVisible = false;
let sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
let searchTimeout = null;
let currentSearchQuery = '';
let currentActionsMessageId = null;
let pendingNewMessages = 0;
let isUserAtBottom = true;
let scrollTimeout = null;

// ========== ОПРЕДЕЛЕНИЕ МОБИЛЬНОГО УСТРОЙСТВА ==========
function isMobile() {
    return window.innerWidth <= 768;
}

// ========== ФУНКЦИЯ ОБНОВЛЕНИЯ ЗАГОЛОВКА ==========
function updateCrmTitle(botName) {
    const botNameTag = document.getElementById('botNameTag');
    let displayName = botName;
    if (displayName && !displayName.startsWith('@')) {
        displayName = '@' + displayName;
    }
    if (botNameTag) {
        botNameTag.textContent = displayName || '@RobotChoiceBot';
    }
}

// ========== ЗАГРУЗКА БОТОВ ==========
async function loadBots() {
    try {
        const res = await fetch('/api/bots');
        const data = await res.json();
        const selector = document.getElementById('botSelector');
        
        if (data.bots && data.bots.length) {
            selector.innerHTML = data.bots.map(bot => 
                `<option value="${bot.id}" ${bot.is_active ? 'selected' : ''}>${bot.name}</option>`
            ).join('');
            
            const activeBot = data.bots.find(bot => bot.is_active);
            if (activeBot) {
                updateCrmTitle(activeBot.name);
            }
        } else {
            selector.innerHTML = '<option value="">Нет ботов</option>';
        }
    } catch(e) {
        console.error('Error loading bots:', e);
    }
}

async function switchBot(botId) {
    try {
        const res = await fetch('/api/bots/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bot_id: parseInt(botId) })
        });
        
        if (res.ok) {
            const data = await res.json();
            updateCrmTitle(data.bot_name);
            currentChatId = null;
            document.getElementById('messagesArea').style.display = 'none';
            await loadChats();
            showToast('✅ Бот переключён на ' + data.bot_name);
        }
    } catch(e) {
        console.error('Error switching bot:', e);
        showToast('❌ Ошибка переключения бота');
    }
}

function showToast(message) {
    let toast = document.querySelector('.toast');
    if (toast) toast.remove();
    toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerText = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

document.getElementById('botSelector')?.addEventListener('change', (e) => {
    if (e.target.value) switchBot(e.target.value);
});

// ========== ВКЛАДКИ ==========
function switchTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    
    const tabContent = document.getElementById(`${tabId}Tab`);
    if (tabContent) {
        tabContent.classList.add('active');
    }
    
    const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    if (tabBtn) {
        tabBtn.classList.add('active');
    }
    
    if (tabId === 'utm') {
        loadUtmStats();
    }
}

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// ========== UTM СТАТИСТИКА ==========
async function loadUtmStats() {
    try {
        const res = await fetch('/api/utm_stats');
        const data = await res.json();
        
        const sourcesContainer = document.getElementById('utmSourcesList');
        if (data.sources && data.sources.length) {
            sourcesContainer.innerHTML = data.sources.map(s => `
                <div class="stats-item">
                    <span class="stats-name">${escapeHtml(s.utm_source)}</span>
                    <span class="stats-count">${s.count}</span>
                </div>
            `).join('');
        } else {
            sourcesContainer.innerHTML = '<div class="empty-state">Нет данных</div>';
        }
        
        const campaignsContainer = document.getElementById('utmCampaignsList');
        if (data.campaigns && data.campaigns.length) {
            campaignsContainer.innerHTML = data.campaigns.map(c => `
                <div class="stats-item">
                    <span class="stats-name">${escapeHtml(c.utm_campaign)}</span>
                    <span class="stats-count">${c.count}</span>
                </div>
            `).join('');
        } else {
            campaignsContainer.innerHTML = '<div class="empty-state">Нет данных</div>';
        }
        
        const conversionsContainer = document.getElementById('utmConversionsList');
        if (data.conversions && data.conversions.length) {
            conversionsContainer.innerHTML = data.conversions.map(c => `
                <div class="stats-item">
                    <span class="stats-name">${escapeHtml(c.utm_source)}</span>
                    <span class="stats-count">${c.count}</span>
                </div>
            `).join('');
        } else {
            conversionsContainer.innerHTML = '<div class="empty-state">Нет данных</div>';
        }
    } catch(e) {
        console.error('Error loading UTM stats:', e);
    }
}

// ========== СВОРАЧИВАНИЕ ПАНЕЛИ ==========
function toggleSidebar() {
    const sidebar = document.getElementById('chatsSidebar');
    const showBtn = document.getElementById('showSidebarBtn');
    const collapseIcon = document.getElementById('collapseIcon');
    const messagesHeader = document.getElementById('messagesHeader');
    
    sidebarCollapsed = !sidebarCollapsed;
    localStorage.setItem('sidebarCollapsed', sidebarCollapsed);
    
    if (sidebarCollapsed) {
        sidebar.classList.add('collapsed');
        showBtn.classList.add('visible');
        if (collapseIcon) collapseIcon.innerHTML = '▶';
        messagesHeader?.classList.add('compact-mode');
    } else {
        sidebar.classList.remove('collapsed');
        showBtn.classList.remove('visible');
        if (collapseIcon) collapseIcon.innerHTML = '◀';
        messagesHeader?.classList.remove('compact-mode');
    }
}

document.getElementById('collapseBtn')?.addEventListener('click', toggleSidebar);
document.getElementById('showSidebarBtn')?.addEventListener('click', () => {
    if (sidebarCollapsed) toggleSidebar();
});

function initSidebarState() {
    const sidebar = document.getElementById('chatsSidebar');
    const showBtn = document.getElementById('showSidebarBtn');
    const collapseIcon = document.getElementById('collapseIcon');
    const messagesHeader = document.getElementById('messagesHeader');
    
    if (sidebarCollapsed) {
        sidebar?.classList.add('collapsed');
        showBtn?.classList.add('visible');
        if (collapseIcon) collapseIcon.innerHTML = '▶';
        messagesHeader?.classList.add('compact-mode');
    } else {
        sidebar?.classList.remove('collapsed');
        showBtn?.classList.remove('visible');
        if (collapseIcon) collapseIcon.innerHTML = '◀';
        messagesHeader?.classList.remove('compact-mode');
    }
}

// ========== МОБИЛЬНАЯ КНОПКА "НАЗАД К ЧАТАМ" ==========
function showChatsListMobile() {
    const sidebar = document.getElementById('chatsSidebar');
    const showBtn = document.getElementById('showSidebarBtn');
    sidebar.classList.remove('mobile-hidden');
    sidebar.classList.remove('collapsed');
    showBtn.classList.remove('visible');
    document.getElementById('mobileBackBtn')?.classList.remove('visible');
    document.getElementById('mobileBackBtnCompact')?.classList.remove('visible');
}

document.getElementById('mobileBackBtn')?.addEventListener('click', showChatsListMobile);
document.getElementById('mobileBackBtnCompact')?.addEventListener('click', showChatsListMobile);

// ========== ФИЛЬТРЫ ==========
function toggleFilterPanel() {
    filterPanelVisible = !filterPanelVisible;
    const panel = document.getElementById('filterPanel');
    const btn = document.getElementById('filterToggleBtn');
    
    if (filterPanelVisible) {
        panel?.classList.add('show');
        btn?.classList.add('active');
    } else {
        panel?.classList.remove('show');
        btn?.classList.remove('active');
    }
}

function updateFilterBadge() {
    const badge = document.getElementById('filterBadge');
    if (currentStatusFilter !== 'all') {
        badge.style.display = 'inline-block';
        const statusMap = {
            'первое сообщение': '🆕',
            'ожидание менеджера': '📞',
            'в работе': '⚙️',
            'закрыт': '✅'
        };
        badge.textContent = statusMap[currentStatusFilter] || '1';
    } else {
        badge.style.display = 'none';
    }
}

function clearFilters() {
    currentStatusFilter = 'all';
    document.querySelectorAll('.status-filter-btn').forEach(btn => {
        if (btn.dataset.status === 'all') {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    updateFilterBadge();
    loadChats();
    if (filterPanelVisible) toggleFilterPanel();
}

document.getElementById('filterToggleBtn')?.addEventListener('click', toggleFilterPanel);
document.getElementById('clearFilterBtn')?.addEventListener('click', clearFilters);

function initStatusFilter() {
    const filterBtns = document.querySelectorAll('.status-filter-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentStatusFilter = btn.dataset.status;
            updateFilterBadge();
            loadChats();
            if (filterPanelVisible) toggleFilterPanel();
        });
    });
}

// ========== ФОРМАТИРОВАНИЕ ДАТЫ ==========
function formatLastMessageTime(timestamp) {
    if (!timestamp) return '';
    
    const date = new Date(timestamp);
    // Переводим в МСК (UTC+3)
    const mskDate = new Date(date.toLocaleString('en-US', { timeZone: 'Europe/Moscow' }));
    const now = new Date();
    const mskNow = new Date(now.toLocaleString('en-US', { timeZone: 'Europe/Moscow' }));
    
    const today = new Date(mskNow.getFullYear(), mskNow.getMonth(), mskNow.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const msgDate = new Date(mskDate.getFullYear(), mskDate.getMonth(), mskDate.getDate());
    
    const timeStr = mskDate.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    
    if (msgDate.getTime() === today.getTime()) {
        return `Сегодня ${timeStr}`;
    } else if (msgDate.getTime() === yesterday.getTime()) {
        return `Вчера ${timeStr}`;
    } else {
        return mskDate.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
    }
}

// ========== ЗАГРУЗКА ЧАТОВ ==========
function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getStatusBadgeClass(status) {
    const map = {
        'первое сообщение': 'new',
        'ожидание менеджера': 'waiting',
        'в работе': 'working',
        'закрыт': 'closed'
    };
    return map[status] || 'new';
}

function getStatusDisplayName(status) {
    const map = {
        'первое сообщение': '🆕 Первое сообщение',
        'ожидание менеджера': '📞 Ожидает менеджера',
        'в работе': '⚙️ В работе',
        'закрыт': '✅ Закрыт'
    };
    return map[status] || status;
}

async function loadChats() {
    try {
        let url = `/api/chats/search?limit=200`;
        if (currentSearchQuery && currentSearchQuery.trim()) {
            url += `&q=${encodeURIComponent(currentSearchQuery.trim())}`;
        }
        if (currentStatusFilter !== 'all') {
            url += `&status=${encodeURIComponent(currentStatusFilter)}`;
        }
        
        const res = await fetch(url);
        const data = await res.json();
        const container = document.getElementById('chatsList');
        
        if (!data.chats || !data.chats.length) {
            container.innerHTML = '<div class="empty-state">❌ Ничего не найдено</div>';
            return;
        }
        
        container.innerHTML = data.chats.map(chat => {
            const statusClass = getStatusBadgeClass(chat.dialog_status);
            const statusDisplay = getStatusDisplayName(chat.dialog_status);
            let displayName = escapeHtml(chat.full_name || chat.username || 'User');
            const lastMessageTime = formatLastMessageTime(chat.last_message_at);
            
            let lastMessageText = '';
            if (chat.last_message_text) {
                lastMessageText = escapeHtml(chat.last_message_text.substring(0, 60));
                if (chat.last_message_text.length > 60) lastMessageText += '...';
            } else {
                lastMessageText = 'Нет сообщений';
            }
            
            if (currentSearchQuery && currentSearchQuery.trim()) {
                const regex = new RegExp(`(${escapeRegex(currentSearchQuery)})`, 'gi');
                displayName = displayName.replace(regex, '<mark>$1</mark>');
            }
            
            return `
                <div class="chat-item" onclick="selectChat(${chat.id})" data-chat-id="${chat.id}">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                        <div class="chat-name">${displayName}</div>
                        <div class="chat-time ${statusClass === 'new' ? 'chat-time-new' : ''}">${lastMessageTime}</div>
                    </div>
                    <div class="status-badge ${statusClass}">${statusDisplay}</div>
                    ${chat.utm_source ? `<div class="utm-badge">📊 ${escapeHtml(chat.utm_source)}</div>` : ''}
                    <div class="chat-preview">${lastMessageText}</div>
                </div>
            `;
        }).join('');
    } catch(e) {
        console.error('Search error:', e);
    }
}

const searchInput = document.getElementById('searchChatsInput');
if (searchInput) {
    searchInput.addEventListener('input', (e) => {
        if (searchTimeout) clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentSearchQuery = e.target.value;
            loadChats();
        }, 300);
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========== МЕНЮ ДЕЙСТВИЙ ==========
function toggleMessageActions(messageId, event) {
    event.stopPropagation();
    const menu = document.getElementById(`actions-menu-${messageId}`);
    if (currentActionsMessageId && currentActionsMessageId !== messageId) {
        const prevMenu = document.getElementById(`actions-menu-${currentActionsMessageId}`);
        if (prevMenu) prevMenu.classList.remove('show');
    }
    if (menu) menu.classList.toggle('show');
    currentActionsMessageId = menu.classList.contains('show') ? messageId : null;
}

document.addEventListener('click', () => {
    if (currentActionsMessageId) {
        const menu = document.getElementById(`actions-menu-${currentActionsMessageId}`);
        if (menu) menu.classList.remove('show');
        currentActionsMessageId = null;
    }
});

async function pinMessage(messageId, text) {
    showToast(`📌 Сообщение закреплено: ${text.substring(0, 50)}...`);
}

async function resendMessage(messageId, text) {
    if (!currentChatId) return;
    const res = await fetch(`/api/chats/${currentChatId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
    });
    if (res.ok) showToast('✅ Сообщение переотправлено');
    else showToast('❌ Ошибка переотправки');
}

async function deleteMessage(messageId) {
    if (confirm('Удалить это сообщение?')) {
        showToast('🗑 Сообщение удалено');
        if (currentChatId) loadMessagesSmart(currentChatId);
    }
}

// ========== МЕДИА ==========
function openMediaModal(url, type) {
    const modal = document.getElementById('mediaModal');
    const img = document.getElementById('mediaImage');
    const video = document.getElementById('mediaVideo');
    img.style.display = 'none';
    video.style.display = 'none';
    if (type === 'image') {
        img.src = url;
        img.style.display = 'block';
    } else if (type === 'video') {
        video.src = url;
        video.style.display = 'block';
        video.load();
    }
    modal.classList.add('active');
}

function closeMediaModal() {
    const modal = document.getElementById('mediaModal');
    const video = document.getElementById('mediaVideo');
    if (video) video.pause();
    modal.classList.remove('active');
}

// ========== HTML СООБЩЕНИЯ ==========
function createMessageHTML(msg) {
    function getSenderInfo(senderType) {
        switch(senderType) {
            case 'user': return { icon: '👤', name: 'Клиент' };
            case 'bot': return { icon: '🤖', name: 'RobotChoiceBot' };
            case 'operator': return { icon: '👨‍💼', name: 'Оператор' };
            default: return { icon: '💬', name: 'Сообщение' };
        }
    }
    
    const sender = getSenderInfo(msg.sender_type);
    const time = new Date(msg.timestamp).toLocaleTimeString('ru-RU', { timeZone: 'Europe/Moscow' });
    const messageText = escapeHtml(msg.message_text || '[Сообщение]');
    const messageLines = messageText.split('\n').map(line => `<p>${line || '<br>'}</p>`).join('');
    
    const isImage = msg.file_id && (msg.message_text?.includes('.jpg') || msg.message_text?.includes('.png') || msg.message_text?.includes('.jpeg') || msg.message_text?.includes('.gif') || msg.message_text?.includes('.webp'));
    const isVideo = msg.file_id && (msg.message_text?.includes('.mp4') || msg.message_text?.includes('.webm') || msg.message_text?.includes('.mov'));
    const mediaHtml = isImage ? `<div class="message-media" onclick="openMediaModal('${msg.file_id}', 'image')"><img src="${msg.file_id}" alt="Вложение"></div>` : '';
    const videoHtml = isVideo ? `<div class="message-media" onclick="openMediaModal('${msg.file_id}', 'video')"><video src="${msg.file_id}" style="width:100%; border-radius:12px;"></video></div>` : '';
    
    return `
        <div class="message ${msg.sender_type}" data-message-id="${msg.id}">
            <div class="message-sender">
                <span class="sender-icon">${sender.icon}</span>
                <span class="sender-name">${escapeHtml(sender.name)}</span>
                <div class="message-actions" onclick="toggleMessageActions(${msg.id}, event)">
                    <span class="dots">⋮</span>
                    <div class="actions-menu" id="actions-menu-${msg.id}">
                        <div class="action-item" onclick="pinMessage(${msg.id}, '${messageText.replace(/'/g, "\\'")}')"><span>📌</span> Закрепить</div>
                        <div class="action-item" onclick="resendMessage(${msg.id}, '${messageText.replace(/'/g, "\\'")}')"><span>🔄</span> Переотправить</div>
                        <div class="action-item" onclick="deleteMessage(${msg.id})"><span>🗑</span> Удалить</div>
                    </div>
                </div>
            </div>
            <div class="message-text">${messageLines}</div>
            ${mediaHtml}
            ${videoHtml}
            <div class="message-time">
                ${time}
                ${msg.sender_type === 'operator' ? '<span class="read-status read">✓✓</span>' : ''}
            </div>
        </div>
    `;
}

// ========== УМНОЕ ОБНОВЛЕНИЕ СООБЩЕНИЙ ==========
let lastMessageCount = 0;
let lastMessageId = null;
let isUpdating = false;

async function loadMessagesSmart(chatId, preserveScroll = true) {
    if (isUpdating) return;
    isUpdating = true;
    
    const container = document.getElementById('messagesContainer');
    if (!container) { isUpdating = false; return; }
    
    const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    const oldScrollHeight = container.scrollHeight;
    const oldScrollTop = container.scrollTop;
    
    try {
        const res = await fetch(`/api/chats/${chatId}/messages?limit=200`);
        const data = await res.json();
        
        if (!data.messages || !data.messages.length) {
            if (lastMessageCount === 0) {
                container.innerHTML = '<div class="empty-state">💬 Нет сообщений</div>';
            }
            isUpdating = false;
            return;
        }
        
        const newLastMessageId = data.messages[data.messages.length - 1].id;
        
        if (lastMessageId === newLastMessageId && lastMessageCount === data.messages.length) {
            isUpdating = false;
            return;
        }
        
        const currentMessages = container.querySelectorAll('.message');
        const currentIds = Array.from(currentMessages).map(el => parseInt(el.dataset.messageId));
        const newIds = data.messages.map(m => m.id);
        
        const needsFullReload = currentIds.some(id => !newIds.includes(id)) || 
                                newIds.some(id => !currentIds.includes(id));
        
        if (needsFullReload || currentMessages.length === 0) {
            container.innerHTML = data.messages.map(msg => createMessageHTML(msg)).join('');
            lastMessageCount = data.messages.length;
            lastMessageId = newLastMessageId;
            
            if (preserveScroll && !wasAtBottom) {
                const newScrollHeight = container.scrollHeight;
                const scrollDiff = newScrollHeight - oldScrollHeight;
                container.scrollTop = oldScrollTop + scrollDiff;
            } else {
                container.scrollTop = container.scrollHeight;
                pendingNewMessages = 0;
                updateNewMessagesButton();
            }
        } else {
            const existingIds = currentIds;
            const newMessages = data.messages.filter(msg => !existingIds.includes(msg.id));
            
            if (newMessages.length > 0) {
                newMessages.forEach(msg => {
                    const messageHtml = createMessageHTML(msg);
                    container.insertAdjacentHTML('beforeend', messageHtml);
                });
                lastMessageCount = data.messages.length;
                lastMessageId = newLastMessageId;
                
                if (wasAtBottom) {
                    container.scrollTop = container.scrollHeight;
                    pendingNewMessages = 0;
                    updateNewMessagesButton();
                } else {
                    pendingNewMessages += newMessages.length;
                    updateNewMessagesButton();
                    
                    const btn = document.getElementById('newMessagesBtn');
                    if (btn && btn.style.display !== 'none') {
                        btn.style.transform = 'scale(1.1)';
                        setTimeout(() => { if (btn) btn.style.transform = ''; }, 200);
                    }
                }
                
                newMessages.forEach(() => {
                    const lastMessage = container.lastElementChild;
                    if (lastMessage) {
                        lastMessage.classList.add('new-message');
                        setTimeout(() => lastMessage.classList.remove('new-message'), 500);
                    }
                });
            }
        }
    } catch(e) {
        console.error('Error loading messages:', e);
    }
    isUpdating = false;
}

async function forceLoadMessages(chatId) {
    lastMessageCount = 0;
    lastMessageId = null;
    await loadMessagesSmart(chatId, false);
}

// ========== КНОПКА "НОВЫЕ СООБЩЕНИЯ" ==========
function checkIfAtBottom() {
    const container = document.getElementById('messagesContainer');
    if (!container) return true;
    
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    if (atBottom !== isUserAtBottom) {
        isUserAtBottom = atBottom;
        if (isUserAtBottom) {
            pendingNewMessages = 0;
            updateNewMessagesButton();
        } else {
            updateNewMessagesButton();
        }
    }
    return atBottom;
}

function updateNewMessagesButton() {
    const btn = document.getElementById('newMessagesBtn');
    const countSpan = document.getElementById('newMessagesCount');
    if (!btn || !countSpan) return;
    
    if (pendingNewMessages > 0 && !isUserAtBottom) {
        countSpan.textContent = pendingNewMessages > 99 ? '99+' : pendingNewMessages;
        btn.style.display = 'flex';
        btn.classList.add('show');
    } else {
        btn.style.display = 'none';
        btn.classList.remove('show');
    }
}

function scrollToBottomMessages() {
    const container = document.getElementById('messagesContainer');
    if (!container) return;
    
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    
    setTimeout(() => {
        if (checkIfAtBottom()) {
            pendingNewMessages = 0;
            updateNewMessagesButton();
        }
    }, 300);
}

function initNewMessagesButton() {
    const container = document.getElementById('messagesContainer');
    const btn = document.getElementById('newMessagesBtn');
    if (!container || !btn) return;
    
    container.addEventListener('scroll', () => {
        if (scrollTimeout) clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => { checkIfAtBottom(); }, 150);
    });
    
    btn.addEventListener('click', scrollToBottomMessages);
    
    const resizeObserver = new ResizeObserver(() => { checkIfAtBottom(); });
    resizeObserver.observe(container);
    
    checkIfAtBottom();
}

// ========== UTM ДЛЯ ЧАТА ==========
async function loadUtmForChat(chatId) {
    try {
        const res = await fetch(`/api/chats/${chatId}/full`);
        const chat = await res.json();
        
        const utmBlock = document.getElementById('utmInfoBlock');
        const utmContent = document.getElementById('utmContent');
        
        const utmFields = [
            { key: 'utm_source', label: '📡 Источник', value: chat.utm_source },
            { key: 'utm_medium', label: '📊 Тип трафика', value: chat.utm_medium },
            { key: 'utm_campaign', label: '🎯 Кампания', value: chat.utm_campaign },
            { key: 'utm_term', label: '🔑 Ключевое слово', value: chat.utm_term },
            { key: 'utm_content', label: '📝 Контент', value: chat.utm_content }
        ];
        
        const hasAnyUtm = utmFields.some(f => f.value);
        
        if (hasAnyUtm) {
            utmContent.innerHTML = utmFields
                .filter(f => f.value)
                .map(f => `
                    <div class="utm-field">
                        <span class="utm-label">${f.label}</span>
                        <span class="utm-value">${escapeHtml(f.value)}</span>
                    </div>
                `).join('');
            utmBlock.style.display = 'block';
        } else {
            utmContent.innerHTML = '<div style="grid-column: span 2; text-align: center; color: var(--text-muted);">Нет UTM-меток</div>';
            utmBlock.style.display = 'block';
        }
        
        if (chat.referrer || chat.gclid || chat.start_param) {
            const extraDiv = document.createElement('div');
            extraDiv.style.marginTop = '8px';
            extraDiv.style.paddingTop = '6px';
            extraDiv.style.borderTop = '1px solid var(--border)';
            extraDiv.style.fontSize = '9px';
            extraDiv.style.display = 'flex';
            extraDiv.style.flexDirection = 'column';
            extraDiv.style.gap = '4px';
            extraDiv.className = 'extra-info';
            
            let extraHtml = '';
            if (chat.referrer) extraHtml += `<div><span>🔄 Referrer:</span> <span>${escapeHtml(chat.referrer)}</span></div>`;
            if (chat.gclid) extraHtml += `<div><span>🔗 GCLID:</span> <span>${escapeHtml(chat.gclid.substring(0, 30))}...</span></div>`;
            if (chat.start_param) extraHtml += `<div><span>🚀 Start param:</span> <span>${escapeHtml(chat.start_param)}</span></div>`;
            
            extraDiv.innerHTML = extraHtml;
            utmContent.appendChild(extraDiv);
        }
    } catch(e) {
        console.error('Error loading UTM:', e);
    }
}

// ========== ИСТОРИЯ UTM ==========
async function showUtmHistory() {
    if (!currentChatId) { showToast('❌ Сначала выберите чат'); return; }
    
    try {
        const chatRes = await fetch(`/api/chats/${currentChatId}/full`);
        const chat = await chatRes.json();
        const userId = chat.user_id;
        if (!userId) { showToast('❌ Не удалось определить пользователя'); return; }
        
        const sessionsRes = await fetch(`/api/users/${userId}/sessions`);
        const sessionsData = await sessionsRes.json();
        const modalBody = document.getElementById('utmHistoryBody');
        
        if (!sessionsData.sessions || sessionsData.sessions.length === 0) {
            modalBody.innerHTML = '<div class="utm-empty">📭 Нет истории UTM-меток</div>';
            document.getElementById('utmHistoryModal').classList.add('active');
            return;
        }
        
        let tableHtml = `<table class="utm-history-table"><thead><tr><th>📅 Дата и время</th><th>📡 Источник</th><th>📊 Тип</th><th>🎯 Кампания</th><th>🔑 Ключевое слово</th><th>📝 Контент</th></tr></thead><tbody>`;
        
        for (const session of sessionsData.sessions) {
            const date = new Date(session.first_interaction);
            const formattedDate = date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
            const source = session.utm_source?.trim() || '-';
            const medium = session.utm_medium?.trim() || '-';
            const campaign = session.utm_campaign?.trim() || '-';
            const term = session.utm_term?.trim() || '-';
            const content = session.utm_content?.trim() || '-';
            
            tableHtml += `<tr><td class="utm-history-date">${formattedDate}</td><td class="utm-history-value">${escapeHtml(source)}</td><td class="utm-history-value">${escapeHtml(medium)}</td><td class="utm-history-value">${escapeHtml(campaign)}</td><td class="utm-history-value">${escapeHtml(term)}</td><td class="utm-history-value">${escapeHtml(content)}</td></tr>`;
        }
        
        tableHtml += `</tbody></table><div style="margin-top: 12px; padding: 8px; background: var(--accent-light); border-radius: 8px; font-size: 10px; color: var(--text-secondary);">📊 Всего визитов с UTM: ${sessionsData.sessions.length}</div>`;
        
        modalBody.innerHTML = tableHtml;
        document.getElementById('utmHistoryModal').classList.add('active');
    } catch(e) {
        console.error('Error loading UTM history:', e);
        showToast('❌ Ошибка загрузки истории UTM');
    }
}

function closeUtmHistoryModal() {
    document.getElementById('utmHistoryModal').classList.remove('active');
}

// ========== ВЫБОР ЧАТА ==========
window.selectChat = async function(chatId) {
    currentChatId = chatId;
    pendingNewMessages = 0;
    isUserAtBottom = true;
    const newBtn = document.getElementById('newMessagesBtn');
    if (newBtn) newBtn.style.display = 'none';
    
    document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
    const selectedElement = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
    if (selectedElement) selectedElement.classList.add('active');
    
    if (isMobile()) {
        const sidebar = document.getElementById('chatsSidebar');
        const showBtn = document.getElementById('showSidebarBtn');
        sidebar.classList.add('mobile-hidden');
        showBtn.classList.add('visible');
        document.getElementById('mobileBackBtn')?.classList.add('visible');
        document.getElementById('mobileBackBtnCompact')?.classList.add('visible');
    }
    
    try {
        const chatRes = await fetch(`/api/chats/${chatId}/full`);
        const chat = await chatRes.json();
        
        const chatName = chat.full_name || chat.username || 'User';
        document.getElementById('chatName').innerText = chatName;
        document.getElementById('chatNameCompact').innerText = chatName;
        document.getElementById('statusSelect').value = chat.dialog_status || 'первое сообщение';
        document.getElementById('compactStatus').innerHTML = getStatusIcon(chat.dialog_status);
        
        currentAutoMode = chat.auto_mode;
        const autoBtn = document.getElementById('toggleAutoBtn');
        const compactAutoBtn = document.getElementById('compactAutoBtn');
        autoBtn.textContent = currentAutoMode ? 'Авторежим ВКЛ' : 'Авторежим ВЫКЛ';
        autoBtn.className = currentAutoMode ? 'toggle-auto-btn' : 'toggle-auto-btn off';
        compactAutoBtn.style.opacity = currentAutoMode ? '1' : '0.5';
        
        await loadUtmForChat(chatId);
        document.getElementById('messagesArea').style.display = 'flex';
        await forceLoadMessages(chatId);
        await fetch(`/api/chats/${chatId}/mark-read`, { method: 'POST' });
    } catch(e) {
        console.error('Error loading chat:', e);
    }
};

function getStatusIcon(status) {
    const icons = { 'первое сообщение': '🆕', 'ожидание менеджера': '📞', 'в работе': '⚙️', 'закрыт': '✅' };
    return icons[status] || '📌';
}

// ========== ОТПРАВКА СООБЩЕНИЯ ==========
async function sendMessage() {
    const input = document.getElementById('messageInput');
    const text = input.value.trim();
    if (!text || !currentChatId) return;
    
    const res = await fetch(`/api/chats/${currentChatId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
    });
    
    if (res.ok) {
        input.value = '';
        lastMessageCount = 0;
        lastMessageId = null;
        await loadMessagesSmart(currentChatId, false);
        setTimeout(() => {
            const container = document.getElementById('messagesContainer');
            if (container) {
                container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
                pendingNewMessages = 0;
                updateNewMessagesButton();
            }
        }, 100);
    }
}

// ========== ОБНОВЛЕНИЕ СТАТУСА ==========
async function updateStatus() {
    const status = document.getElementById('statusSelect').value;
    if (!currentChatId) return;
    await fetch(`/api/chats/${currentChatId}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status })
    });
    document.getElementById('compactStatus').innerHTML = getStatusIcon(status);
    loadChats();
}

// ========== АВТОРЕЖИМ ==========
async function toggleAutoMode() {
    if (!currentChatId) return;
    const res = await fetch(`/api/chats/${currentChatId}/toggle-auto`, { method: 'POST' });
    if (res.ok) {
        currentAutoMode = !currentAutoMode;
        const autoBtn = document.getElementById('toggleAutoBtn');
        const compactAutoBtn = document.getElementById('compactAutoBtn');
        autoBtn.textContent = currentAutoMode ? 'Авторежим ВКЛ' : 'Авторежим ВЫКЛ';
        autoBtn.className = currentAutoMode ? 'toggle-auto-btn' : 'toggle-auto-btn off';
        compactAutoBtn.style.opacity = currentAutoMode ? '1' : '0.5';
    }
}

// ========== ШАБЛОНЫ ==========
let templates = [];

async function loadTemplates() {
    try {
        const res = await fetch('/api/templates');
        const data = await res.json();
        templates = data.templates;
        renderTemplatesList();
    } catch(e) { console.error(e); }
}

function renderTemplatesList() {
    const container = document.getElementById('templatesList');
    if (!templates.length) {
        container.innerHTML = '<div class="empty-state">Нет шаблонов. Создайте первый!</div>';
        return;
    }
    container.innerHTML = templates.map(tpl => `
        <div class="template-item" onclick="useTemplate(${tpl.id})">
            <div class="template-title">${escapeHtml(tpl.title)}</div>
            <div class="template-text">${escapeHtml(tpl.text.substring(0, 100))}${tpl.text.length > 100 ? '...' : ''}</div>
        </div>
    `).join('');
}

window.useTemplate = function(templateId) {
    const template = templates.find(t => t.id === templateId);
    if (template && currentChatId) {
        document.getElementById('messageInput').value = template.text;
        document.getElementById('messageInput').focus();
        closeTemplatesModal();
    }
};

window.addTemplate = async function() {
    const title = document.getElementById('newTemplateTitle').value.trim();
    const text = document.getElementById('newTemplateText').value.trim();
    if (!title || !text) { alert('Заполните название и текст шаблона'); return; }
    
    const res = await fetch('/api/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, text })
    });
    
    if (res.ok) {
        document.getElementById('newTemplateTitle').value = '';
        document.getElementById('newTemplateText').value = '';
        await loadTemplates();
    } else { alert('Ошибка добавления шаблона'); }
};

function openTemplatesModal() {
    document.getElementById('templatesModal').classList.add('active');
    loadTemplates();
}

window.closeTemplatesModal = function() {
    document.getElementById('templatesModal').classList.remove('active');
};

// ========== ЭКСПОРТ ==========
async function exportPeriod() {
    const dateFrom = document.getElementById('exportDateFrom').value;
    const dateTo = document.getElementById('exportDateTo').value;
    if (!dateFrom || !dateTo) { alert('Выберите период (с и по дату)'); return; }
    window.open(`/api/export/period?from_date=${dateFrom}&to_date=${dateTo}`, '_blank');
}

// ========== КОМПАКТНЫЕ КНОПКИ ==========
document.getElementById('compactAutoBtn')?.addEventListener('click', () => { if (currentChatId) toggleAutoMode(); });
document.getElementById('compactTemplatesBtn')?.addEventListener('click', openTemplatesModal);
document.getElementById('compactExportBtn')?.addEventListener('click', () => {
    const dateFrom = document.getElementById('exportDateFrom').value;
    const dateTo = document.getElementById('exportDateTo').value;
    if (!dateFrom || !dateTo) { alert('Выберите период (с и по дату)'); return; }
    window.open(`/api/export/period?from_date=${dateFrom}&to_date=${dateTo}`, '_blank');
});

// ========== ОБРАБОТЧИКИ ==========
document.getElementById('statusSelect')?.addEventListener('change', updateStatus);
document.getElementById('toggleAutoBtn')?.addEventListener('click', toggleAutoMode);
document.getElementById('sendBtn')?.addEventListener('click', sendMessage);
document.getElementById('templatesBtn')?.addEventListener('click', openTemplatesModal);
document.getElementById('exportPeriodBtn')?.addEventListener('click', exportPeriod);
document.getElementById('showUtmHistoryBtn')?.addEventListener('click', showUtmHistory);
document.getElementById('messageInput')?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ========== ОТСЛЕЖИВАНИЕ ИЗМЕНЕНИЯ РАЗМЕРА ОКНА ==========
window.addEventListener('resize', () => {
    if (!isMobile() && currentChatId) {
        document.getElementById('mobileBackBtn')?.classList.remove('visible');
        document.getElementById('mobileBackBtnCompact')?.classList.remove('visible');
    }
});

// ========== ИНИЦИАЛИЗАЦИЯ ==========
initNewMessagesButton();

setInterval(() => {
    loadChats();
    if (currentChatId) { loadMessagesSmart(currentChatId, true); }
}, 10000);

initStatusFilter();
initSidebarState();
loadBots();
loadChats();
