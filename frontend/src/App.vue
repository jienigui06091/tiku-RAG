<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  FileText,
  FolderPlus,
  LibraryBig,
  Link2,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Search,
  SendHorizontal,
  Settings2,
  Trash2,
  Upload,
  X,
} from "lucide-vue-next";
import ChunkingSettings from "./components/ChunkingSettings.vue";
import {
  api,
  type ChatMessage,
  type ChatSession,
  type ChunkingConfig,
  type DocumentChunk,
  type DocumentRecord,
  type Library,
} from "./api";

const libraries = ref<Library[]>([]);
const chats = ref<ChatSession[]>([]);
const activeLibraryId = ref("");
const activeChatId = ref("");
const chunks = ref<DocumentChunk[]>([]);
const documents = ref<DocumentRecord[]>([]);
const chatMessages = ref<ChatMessage[]>([]);
const totalChunks = ref(0);
const currentPage = ref(1);
const keyword = ref("");
const loading = ref(true);
const listLoading = ref(false);
const chatLoading = ref(false);
const uploadBusy = ref(false);
const reindexBusy = ref(false);
const chatBusy = ref(false);
const chatBindingBusy = ref(false);
const createBusy = ref(false);
const deleteBusy = ref(false);
const error = ref("");
const newLibraryError = ref("");
const newChatError = ref("");
const showNewLibrary = ref(false);
const showNewChat = ref(false);
const showChunkSettings = ref(false);
const libraryPendingDeletion = ref<Library | null>(null);
const chatPendingDeletion = ref<ChatSession | null>(null);
const newLibrary = ref({ name: "", subject: "", description: "" });
const newChat = ref({ title: "", libraryId: "" });
const prompt = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const chunkingConfig = ref<ChunkingConfig>({
  chunk_model: "interface",
  chunk_size: 800,
  chunk_overlap: 120,
  retain_context: true,
  split_by_page: true,
  custom_delimiter: "========================",
});
type PageView = "libraries" | "chats";

function pageFromPath(pathname: string): PageView {
  return pathname === "/chats" ? "chats" : "libraries";
}

const view = ref<PageView>(pageFromPath(window.location.pathname));

const activeLibrary = computed(() => libraries.value.find((library) => library.id === activeLibraryId.value));
const activeChat = computed(() => chats.value.find((chat) => chat.id === activeChatId.value));
const totalPages = computed(() => Math.max(1, Math.ceil(totalChunks.value / 12)));
const pageLabel = computed(() => `${currentPage.value} / ${totalPages.value}`);

function replaceChat(updated: ChatSession) {
  chats.value = chats.value.map((chat) => (chat.id === updated.id ? updated : chat));
}

function navigate(nextView: PageView) {
  const pathname = nextView === "chats" ? "/chats" : "/";
  if (window.location.pathname !== pathname) {
    window.history.pushState({}, "", pathname);
  }
  view.value = nextView;
}

function syncViewFromLocation() {
  view.value = pageFromPath(window.location.pathname);
}

async function loadLibraries() {
  libraries.value = await api.listLibraries();
  if (!activeLibraryId.value && libraries.value.length) {
    activeLibraryId.value = libraries.value[0].id;
  }
}

async function loadChats() {
  chats.value = await api.listChats();
  if (!activeChatId.value && chats.value.length) {
    activeChatId.value = chats.value[0].id;
  }
}

async function loadActiveLibrary() {
  if (!activeLibraryId.value) {
    chunks.value = [];
    documents.value = [];
    totalChunks.value = 0;
    return;
  }

  listLoading.value = true;
  error.value = "";
  try {
    const [page, documentList] = await Promise.all([
      api.listChunks(activeLibraryId.value, currentPage.value, keyword.value),
      api.listDocuments(activeLibraryId.value),
    ]);
    chunks.value = page.items;
    totalChunks.value = page.total;
    documents.value = documentList;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "加载知识库失败。";
  } finally {
    listLoading.value = false;
  }
}

async function loadActiveChat() {
  if (!activeChatId.value) {
    chatMessages.value = [];
    return;
  }

  chatLoading.value = true;
  try {
    chatMessages.value = await api.listChatMessages(activeChatId.value);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "加载聊天记录失败。";
  } finally {
    chatLoading.value = false;
  }
}

function openNewLibrary() {
  newLibraryError.value = "";
  showNewLibrary.value = true;
}

async function createLibrary() {
  const name = newLibrary.value.name.trim();
  if (!name) {
    newLibraryError.value = "请输入知识库名称。";
    return;
  }

  createBusy.value = true;
  newLibraryError.value = "";
  try {
    const library = await api.createLibrary({
      name,
      subject: newLibrary.value.subject.trim() || undefined,
      description: newLibrary.value.description.trim() || undefined,
    });
    libraries.value.unshift(library);
    activeLibraryId.value = library.id;
    newLibrary.value = { name: "", subject: "", description: "" };
    showNewLibrary.value = false;
  } catch (reason) {
    newLibraryError.value = reason instanceof Error ? reason.message : "创建知识库失败。";
  } finally {
    createBusy.value = false;
  }
}

function openNewChat() {
  newChatError.value = "";
  newChat.value = { title: "", libraryId: activeLibraryId.value };
  showNewChat.value = true;
}

async function createChat() {
  createBusy.value = true;
  newChatError.value = "";
  try {
    const chat = await api.createChat({
      title: newChat.value.title.trim() || "新建聊天",
      library_id: newChat.value.libraryId || undefined,
    });
    chats.value.unshift(chat);
    activeChatId.value = chat.id;
    newChat.value = { title: "", libraryId: "" };
    showNewChat.value = false;
  } catch (reason) {
    newChatError.value = reason instanceof Error ? reason.message : "创建聊天失败。";
  } finally {
    createBusy.value = false;
  }
}

function selectLibrary(id: string) {
  if (id === activeLibraryId.value) return;
  activeLibraryId.value = id;
  currentPage.value = 1;
  keyword.value = "";
}

function selectChat(chat: ChatSession) {
  activeChatId.value = chat.id;
}

async function updateChatLibrary(event: Event) {
  const chat = activeChat.value;
  if (!chat) return;

  const libraryId = (event.target as HTMLSelectElement).value || null;
  chatBindingBusy.value = true;
  error.value = "";
  try {
    const updated = await api.updateChat(chat.id, { library_id: libraryId });
    replaceChat(updated);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "更新挂载知识库失败。";
  } finally {
    chatBindingBusy.value = false;
  }
}

function requestDeleteLibrary(library: Library) {
  libraryPendingDeletion.value = library;
}

async function confirmDeleteLibrary() {
  const library = libraryPendingDeletion.value;
  if (!library) return;

  deleteBusy.value = true;
  error.value = "";
  try {
    await api.deleteLibrary(library.id);
    libraries.value = libraries.value.filter((item) => item.id !== library.id);
    await loadChats();
    if (activeLibraryId.value === library.id) {
      activeLibraryId.value = libraries.value[0]?.id || "";
      currentPage.value = 1;
      keyword.value = "";
    }
    libraryPendingDeletion.value = null;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "删除知识库失败。";
  } finally {
    deleteBusy.value = false;
  }
}

function requestDeleteChat(chat: ChatSession) {
  chatPendingDeletion.value = chat;
}

async function confirmDeleteChat() {
  const chat = chatPendingDeletion.value;
  if (!chat) return;

  deleteBusy.value = true;
  error.value = "";
  try {
    await api.deleteChat(chat.id);
    chats.value = chats.value.filter((item) => item.id !== chat.id);
    if (activeChatId.value === chat.id) activeChatId.value = chats.value[0]?.id || "";
    chatPendingDeletion.value = null;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "删除聊天失败。";
  } finally {
    deleteBusy.value = false;
  }
}

function chooseFile() {
  fileInput.value?.click();
}

async function uploadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file || !activeLibraryId.value) return;

  uploadBusy.value = true;
  error.value = "";
  try {
    await api.upload(activeLibraryId.value, file, chunkingConfig.value);
    await loadLibraries();
    await loadActiveLibrary();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "导入文档失败。";
  } finally {
    uploadBusy.value = false;
    if (fileInput.value) fileInput.value.value = "";
  }
}

async function reindexLibrary() {
  if (!activeLibraryId.value || chunkingConfig.value.chunk_overlap >= chunkingConfig.value.chunk_size) return;

  reindexBusy.value = true;
  error.value = "";
  try {
    await api.reindex(activeLibraryId.value, chunkingConfig.value);
    await loadLibraries();
    await loadActiveLibrary();
    showChunkSettings.value = false;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "重新分块失败。";
  } finally {
    reindexBusy.value = false;
  }
}

async function sendMessage() {
  const chat = activeChat.value;
  const query = prompt.value.trim();
  if (!chat || !chat.library_id || !query) return;

  chatBusy.value = true;
  error.value = "";
  try {
    const exchange = await api.sendChatMessage(chat.id, query);
    chatMessages.value.push(exchange.user_message, exchange.assistant_message);
    prompt.value = "";
    await loadChats();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "发送消息失败。";
  } finally {
    chatBusy.value = false;
  }
}

function submitSearch() {
  currentPage.value = 1;
  loadActiveLibrary();
}

function previousPage() {
  if (currentPage.value > 1) currentPage.value -= 1;
}

function nextPage() {
  if (currentPage.value < totalPages.value) currentPage.value += 1;
}

watch([activeLibraryId, currentPage], loadActiveLibrary);
watch(activeChatId, loadActiveChat);

onMounted(async () => {
  window.addEventListener("popstate", syncViewFromLocation);
  try {
    await Promise.all([loadLibraries(), loadChats()]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "无法连接后端服务。";
  } finally {
    loading.value = false;
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("popstate", syncViewFromLocation);
});
</script>

<template>
  <main class="workspace" :class="view === 'chats' ? 'chat-workspace' : 'knowledge-workspace'">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><BookOpen :size="20" /></div>
        <div>
          <strong>Tiku RAG</strong>
          <span>文档检索工作台</span>
        </div>
      </div>

      <nav class="page-navigation" aria-label="主导航">
        <button class="page-navigation-item" :class="{ active: view === 'libraries' }" type="button" @click="navigate('libraries')">
          <LibraryBig :size="17" />
          <span>知识库</span>
        </button>
        <button class="page-navigation-item" :class="{ active: view === 'chats' }" type="button" @click="navigate('chats')">
          <MessageSquareText :size="17" />
          <span>聊天</span>
        </button>
      </nav>

      <section v-if="view === 'libraries'" class="sidebar-section">
        <div class="sidebar-title">
          <span>知识库</span>
          <button class="icon-button" type="button" title="创建知识库" @click="openNewLibrary">
            <FolderPlus :size="17" />
          </button>
        </div>
        <nav class="library-list">
          <p v-if="loading" class="library-loading"><LoaderCircle class="spin" :size="15" />正在加载知识库</p>
          <div v-for="library in libraries" :key="library.id" class="library-row">
            <button
              class="library-entry"
              :class="{ active: library.id === activeLibraryId }"
              type="button"
              @click="selectLibrary(library.id)"
            >
              <LibraryBig :size="17" />
              <span>{{ library.name }}</span>
              <small>{{ library.chunk_count }}</small>
            </button>
            <button
              class="icon-button delete-library-button"
              type="button"
              :title="`删除 ${library.name}`"
              @click="requestDeleteLibrary(library)"
            >
              <Trash2 :size="15" />
            </button>
          </div>
          <p v-if="!loading && !libraries.length" class="empty-side">暂无知识库</p>
        </nav>
      </section>

      <section v-else class="sidebar-section chat-sidebar-section">
        <div class="sidebar-title">
          <span>聊天</span>
          <button class="icon-button" type="button" title="创建聊天" @click="openNewChat"><Plus :size="17" /></button>
        </div>
        <nav class="library-list">
          <div v-for="chat in chats" :key="chat.id" class="library-row">
            <button
              class="library-entry chat-entry"
              :class="{ active: chat.id === activeChatId }"
              type="button"
              @click="selectChat(chat)"
            >
              <MessageSquareText :size="17" />
              <span>{{ chat.title }}</span>
              <small v-if="chat.library_name" title="已挂载知识库"><Link2 :size="12" /></small>
            </button>
            <button
              class="icon-button delete-library-button"
              type="button"
              :title="`删除 ${chat.title}`"
              @click="requestDeleteChat(chat)"
            >
              <Trash2 :size="15" />
            </button>
          </div>
          <p v-if="!loading && !chats.length" class="empty-side">暂无聊天</p>
        </nav>
      </section>

      <div class="sidebar-foot"><CircleHelp :size="16" /><span>PDF / DOCX / TXT / MD</span></div>
    </aside>

    <section v-if="view === 'libraries'" class="content">
      <header class="content-header">
        <div>
          <p class="eyebrow">{{ activeLibrary?.subject || "知识库工作台" }}</p>
          <h1>{{ activeLibrary?.name || "请选择或创建知识库" }}</h1>
          <p class="subhead">
            {{ activeLibrary?.description || "导入文档后，可按接口、标题、固定窗口或自定义分隔符创建可检索分块。" }}
          </p>
        </div>
        <div v-if="activeLibrary" class="header-actions">
          <input ref="fileInput" class="visually-hidden" type="file" accept=".pdf,.docx,.txt,.md" @change="uploadFile" />
          <button class="icon-button bordered" type="button" title="分块设置" @click="showChunkSettings = true">
            <Settings2 :size="17" />
          </button>
          <button class="primary-button" type="button" :disabled="uploadBusy" @click="chooseFile">
            <LoaderCircle v-if="uploadBusy" class="spin" :size="17" />
            <Upload v-else :size="17" />
            {{ uploadBusy ? "正在导入" : "导入文档" }}
          </button>
        </div>
      </header>

      <div v-if="error" class="error-banner">
        <span>{{ error }}</span>
        <button class="icon-button" type="button" title="关闭提示" @click="error = ''"><X :size="16" /></button>
      </div>

      <div v-if="loading" class="state-line initial-loading"><LoaderCircle class="spin" :size="18" />正在加载工作台</div>

      <template v-else-if="activeLibrary">
        <div class="stats-row">
          <div><span>分块数</span><strong>{{ activeLibrary.chunk_count }}</strong></div>
          <div><span>文档数</span><strong>{{ documents.length }}</strong></div>
          <div><span>已就绪</span><strong>{{ documents.filter((item) => item.status === "ready").length }}</strong></div>
        </div>

        <div class="question-toolbar">
          <label class="search-field">
            <Search :size="17" />
            <input v-model="keyword" placeholder="搜索接口、文档或分块内容" @keyup.enter="submitSearch" />
          </label>
          <button class="icon-button bordered" type="button" title="搜索分块" @click="submitSearch"><Search :size="17" /></button>
          <span class="result-count">{{ totalChunks }} 个分块</span>
        </div>

        <div class="question-list">
          <article v-for="chunk in chunks" :key="chunk.id" class="question-item">
            <div class="question-number">{{ chunk.sequence }}</div>
            <div class="question-body">
              <p v-if="chunk.chapter" class="chunk-title">{{ chunk.chapter }}</p>
              <p class="question-stem chunk-content">{{ chunk.content }}</p>
              <div class="source-row">
                <FileText :size="14" />
                <span>{{ chunk.document_name || "已导入文档" }}</span>
                <span v-if="chunk.source_page_start">
                  第 {{ chunk.source_page_start }} 页<template v-if="chunk.source_page_end && chunk.source_page_end !== chunk.source_page_start">-{{ chunk.source_page_end }} 页</template>
                </span>
                <span>字符 {{ chunk.char_start }}-{{ chunk.char_end }}</span>
              </div>
            </div>
          </article>
          <div v-if="listLoading" class="state-line"><LoaderCircle class="spin" :size="18" />正在加载分块</div>
          <div v-else-if="!chunks.length" class="state-line">暂无分块</div>
        </div>

        <footer v-if="totalChunks" class="pagination">
          <button class="icon-button bordered" type="button" title="上一页" :disabled="currentPage <= 1" @click="previousPage">
            <ChevronLeft :size="18" />
          </button>
          <span>{{ pageLabel }}</span>
          <button class="icon-button bordered" type="button" title="下一页" :disabled="currentPage >= totalPages" @click="nextPage">
            <ChevronRight :size="18" />
          </button>
        </footer>
      </template>

      <div v-else class="state-line initial-loading">未选择知识库</div>
    </section>

    <aside v-else class="assistant-panel">
      <header>
        <div class="panel-icon"><MessageSquareText :size="18" /></div>
        <div class="chat-heading">
          <strong>{{ activeChat?.title || "聊天" }}</strong>
          <select
            :value="activeChat?.library_id || ''"
            :disabled="!activeChat || chatBindingBusy"
            aria-label="挂载的知识库"
            @change="updateChatLibrary"
          >
            <option value="">未挂载知识库</option>
            <option v-for="library in libraries" :key="library.id" :value="library.id">{{ library.name }}</option>
          </select>
        </div>
        <button class="icon-button chat-create-button" type="button" title="创建聊天" @click="openNewChat"><Plus :size="17" /></button>
      </header>

      <div class="chat-body">
        <div v-if="chatLoading" class="chat-empty"><LoaderCircle class="spin" :size="20" /></div>
        <div v-else-if="!activeChat" class="chat-empty"><MessageSquareText :size="22" /><p>请先创建聊天。</p></div>
        <div v-else-if="!chatMessages.length" class="chat-empty"><MessageSquareText :size="22" /><p>当前聊天暂无消息。</p></div>
        <template v-else>
          <template v-for="message in chatMessages" :key="message.id">
            <div v-if="message.role === 'user'" class="question-bubble">{{ message.content }}</div>
            <div v-else class="assistant-message">
              <div class="answer-bubble">{{ message.content }}</div>
              <section v-if="message.citations.length" class="citations">
                <p>引用来源</p>
                <div v-for="citation in message.citations" :key="citation.id" class="citation">
                  <FileText :size="15" />
                  <span>
                    {{ citation.document_name || "知识库" }} / {{ citation.chapter || "文档内容" }} / 分块 {{ citation.chunk_sequence }}
                    <template v-if="citation.source_page_start">
                      / 第 {{ citation.source_page_start }} 页<template v-if="citation.source_page_end && citation.source_page_end !== citation.source_page_start">-{{ citation.source_page_end }} 页</template>
                    </template>
                  </span>
                  <small>{{ Math.round(citation.score * 100) }}%</small>
                </div>
              </section>
            </div>
          </template>
        </template>
      </div>

      <form class="chat-form" @submit.prevent="sendMessage">
        <textarea
          v-model="prompt"
          :disabled="!activeChat || !activeChat.library_id || chatBusy"
          :placeholder="activeChat?.library_id ? '请输入关于当前知识库的问题' : '请先挂载知识库再发送消息'"
        ></textarea>
        <button
          class="send-button"
          type="submit"
          title="发送消息"
          :disabled="!activeChat || !activeChat.library_id || !prompt.trim() || chatBusy"
        >
          <LoaderCircle v-if="chatBusy" class="spin" :size="18" />
          <SendHorizontal v-else :size="18" />
        </button>
      </form>
    </aside>

    <div v-if="showNewLibrary" class="modal-backdrop" @click.self="showNewLibrary = false">
      <form class="modal" @submit.prevent="createLibrary">
        <div class="modal-title">
          <h2>创建知识库</h2>
          <button class="icon-button" type="button" title="关闭" @click="showNewLibrary = false"><X :size="18" /></button>
        </div>
        <label>名称<input v-model="newLibrary.name" autofocus required placeholder="例如：订单接口" /></label>
        <label>分类<input v-model="newLibrary.subject" placeholder="例如：后端接口" /></label>
        <label>描述<textarea v-model="newLibrary.description" placeholder="适用范围、版本或备注"></textarea></label>
        <p v-if="newLibraryError" class="modal-error">{{ newLibraryError }}</p>
        <div class="modal-actions">
          <button class="quiet-button" type="button" :disabled="createBusy" @click="showNewLibrary = false">取消</button>
          <button class="primary-button" type="submit" :disabled="createBusy">
            <LoaderCircle v-if="createBusy" class="spin" :size="17" />
            <Plus v-else :size="17" />
            {{ createBusy ? "正在创建" : "创建" }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showNewChat" class="modal-backdrop" @click.self="showNewChat = false">
      <form class="modal" @submit.prevent="createChat">
        <div class="modal-title">
          <h2>创建聊天</h2>
          <button class="icon-button" type="button" title="关闭" @click="showNewChat = false"><X :size="18" /></button>
        </div>
        <label>聊天名称<input v-model="newChat.title" autofocus placeholder="例如：订单接口评审" /></label>
        <label>
          挂载知识库
          <select v-model="newChat.libraryId">
            <option value="">暂不挂载</option>
            <option v-for="library in libraries" :key="library.id" :value="library.id">{{ library.name }}</option>
          </select>
        </label>
        <p v-if="newChatError" class="modal-error">{{ newChatError }}</p>
        <div class="modal-actions">
          <button class="quiet-button" type="button" :disabled="createBusy" @click="showNewChat = false">取消</button>
          <button class="primary-button" type="submit" :disabled="createBusy">
            <LoaderCircle v-if="createBusy" class="spin" :size="17" />
            <Plus v-else :size="17" />
            {{ createBusy ? "正在创建" : "创建" }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="libraryPendingDeletion" class="modal-backdrop" @click.self="!deleteBusy && (libraryPendingDeletion = null)">
      <form class="modal delete-modal" @submit.prevent="confirmDeleteLibrary">
        <div class="modal-title">
          <h2>删除知识库</h2>
          <button class="icon-button" type="button" title="关闭" :disabled="deleteBusy" @click="libraryPendingDeletion = null"><X :size="18" /></button>
        </div>
        <p>
          确认删除“{{ libraryPendingDeletion.name }}”吗？其中的文档和分块将一并删除；已有聊天会保留，但将解除该知识库的挂载。
        </p>
        <div class="modal-actions">
          <button class="quiet-button" type="button" :disabled="deleteBusy" @click="libraryPendingDeletion = null">取消</button>
          <button class="danger-button" type="submit" :disabled="deleteBusy">
            <LoaderCircle v-if="deleteBusy" class="spin" :size="17" />
            <Trash2 v-else :size="17" />
            {{ deleteBusy ? "正在删除" : "删除" }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="chatPendingDeletion" class="modal-backdrop" @click.self="!deleteBusy && (chatPendingDeletion = null)">
      <form class="modal delete-modal" @submit.prevent="confirmDeleteChat">
        <div class="modal-title">
          <h2>删除聊天</h2>
          <button class="icon-button" type="button" title="关闭" :disabled="deleteBusy" @click="chatPendingDeletion = null"><X :size="18" /></button>
        </div>
        <p>确认删除“{{ chatPendingDeletion.title }}”及其聊天记录吗？</p>
        <div class="modal-actions">
          <button class="quiet-button" type="button" :disabled="deleteBusy" @click="chatPendingDeletion = null">取消</button>
          <button class="danger-button" type="submit" :disabled="deleteBusy">
            <LoaderCircle v-if="deleteBusy" class="spin" :size="17" />
            <Trash2 v-else :size="17" />
            {{ deleteBusy ? "正在删除" : "删除" }}
          </button>
        </div>
      </form>
    </div>

    <ChunkingSettings
      v-if="showChunkSettings"
      :config="chunkingConfig"
      :document-count="documents.length"
      :busy="reindexBusy"
      @close="showChunkSettings = false"
      @update:config="chunkingConfig = $event"
      @reindex="reindexLibrary"
    />
  </main>
</template>
