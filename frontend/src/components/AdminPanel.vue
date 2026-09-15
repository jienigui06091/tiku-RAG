<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { LoaderCircle, Plus, Save, UserPlus } from "lucide-vue-next";
import {
  api,
  type CurrentUser,
  type LlmSettings,
  type LlmSettingsUpdate,
  type SystemSettings,
  type SystemSettingsUpdate,
} from "../api";

const props = defineProps<{ mode: "users" | "settings"; currentUser: CurrentUser }>();
const users = ref<CurrentUser[]>([]);
const settings = ref<SystemSettings | null>(null);
const llmSettings = ref<LlmSettings | null>(null);
const userEdits = reactive<Record<string, { role: "member" | "super_admin"; password: string }>>({});
const busy = ref(false);
const error = ref("");
const success = ref("");
const newUser = reactive({ username: "", display_name: "", password: "", role: "member" as "member" | "super_admin" });
const config = reactive<SystemSettingsUpdate>({});
const llmConfig = reactive<LlmSettingsUpdate>({});
const isSuperAdmin = computed(() => props.currentUser.role === "super_admin");

function syncLlmConfig(source: Pick<LlmSettings, "llm_base_url" | "llm_model" | "llm_temperature">) {
  Object.assign(llmConfig, {
    llm_base_url: source.llm_base_url,
    llm_model: source.llm_model,
    llm_temperature: source.llm_temperature,
    llm_api_key: "",
  });
}

async function load() {
  busy.value = true;
  error.value = "";
  try {
    if (props.mode === "users") {
      users.value = await api.listUsers();
      for (const user of users.value) {
        userEdits[user.id] = { role: user.role, password: "" };
      }
    }
    else {
      if (isSuperAdmin.value) {
        settings.value = await api.getSystemSettings();
        Object.assign(config, settings.value);
        syncLlmConfig(settings.value);
      }
      else {
        llmSettings.value = await api.getLlmSettings();
        syncLlmConfig(llmSettings.value);
      }
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "加载失败";
  } finally {
    busy.value = false;
  }
}

async function createUser() {
  busy.value = true;
  error.value = "";
  try {
    const user = await api.createUser(newUser);
    users.value.unshift(user);
    Object.assign(newUser, { username: "", display_name: "", password: "", role: "member" });
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "创建失败";
  } finally {
    busy.value = false;
  }
}

async function toggleUser(user: CurrentUser) {
  busy.value = true;
  error.value = "";
  try {
    const updated = await api.updateUser(user.id, { is_active: !user.is_active });
    users.value = users.value.map((item) => item.id === updated.id ? updated : item);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "更新失败";
  } finally {
    busy.value = false;
  }
}

async function saveUser(user: CurrentUser) {
  const edit = userEdits[user.id];
  if (!edit) return;
  busy.value = true;
  error.value = "";
  try {
    const payload: { role?: "member" | "super_admin"; password?: string } = {};
    if (edit.role !== user.role) payload.role = edit.role;
    if (edit.password.trim()) payload.password = edit.password;
    if (!Object.keys(payload).length) return;
    const updated = await api.updateUser(user.id, payload);
    users.value = users.value.map((item) => item.id === updated.id ? updated : item);
    userEdits[user.id] = { role: updated.role, password: "" };
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "更新失败";
  } finally {
    busy.value = false;
  }
}

async function saveSettings() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (isSuperAdmin.value) {
      const payload = { ...config, ...llmConfig };
      for (const key of ["minio_access_key", "minio_secret_key", "milvus_token", "embedding_api_key", "rerank_api_key", "llm_api_key", "ocr_api_key"] as const) {
        if (!payload[key]) delete payload[key];
      }
      settings.value = await api.updateSystemSettings(payload);
      Object.assign(config, settings.value);
      syncLlmConfig(settings.value);
    }
    else {
      const payload = { ...llmConfig };
      if (!payload.llm_api_key) delete payload.llm_api_key;
      llmSettings.value = await api.updateLlmSettings(payload);
      syncLlmConfig(llmSettings.value);
    }
    success.value = "配置已保存";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "保存失败";
  } finally {
    busy.value = false;
  }
}

onMounted(load);
watch(() => props.mode, load);
</script>

<template>
  <section class="content admin-content">
    <header class="content-header">
      <div>
        <p class="eyebrow">系统管理</p>
        <h1>{{ mode === "users" ? "成员管理" : "运行配置" }}</h1>
        <p class="subhead">{{ mode === "users" ? "创建、停用或重置成员账户。" : isSuperAdmin ? "这些配置即时用于模型、检索和文件处理；部署和启动参数仍保留在环境变量中。" : "你只能修改大语言模型的服务地址、模型、密钥和温度。" }}</p>
      </div>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <p v-if="success" class="success-banner">{{ success }}</p>
    <div v-if="busy && !settings && !llmSettings && !users.length" class="state-line"><LoaderCircle class="spin" :size="18" />正在加载</div>

    <template v-else-if="mode === 'users'">
      <form class="admin-form user-create-form" @submit.prevent="createUser">
        <label>用户名<input v-model="newUser.username" required pattern="[A-Za-z0-9_.-]{3,}" /></label>
        <label>显示名称<input v-model="newUser.display_name" required /></label>
        <label>初始密码<input v-model="newUser.password" type="password" minlength="8" required /></label>
        <label>角色<select v-model="newUser.role"><option value="member">成员</option><option value="super_admin">超级管理员</option></select></label>
        <button class="primary-button" type="submit" :disabled="busy"><UserPlus :size="16" />创建成员</button>
      </form>
      <div class="admin-list">
        <article v-for="user in users" :key="user.id" class="admin-list-row">
          <div><strong>{{ user.display_name }}</strong><span>{{ user.username }} / {{ user.role === "super_admin" ? "超级管理员" : "成员" }}</span></div>
          <div class="user-row-actions">
            <select v-model="userEdits[user.id].role" :disabled="busy">
              <option value="member">成员</option>
              <option value="super_admin">超级管理员</option>
            </select>
            <input v-model="userEdits[user.id].password" type="password" minlength="8" placeholder="新密码（可选）" :disabled="busy" />
            <button class="quiet-button" type="button" :disabled="busy" @click="saveUser(user)">保存</button>
            <button class="quiet-button" type="button" :disabled="busy || user.id === currentUser.id" @click="toggleUser(user)">{{ user.is_active ? "停用" : "启用" }}</button>
          </div>
        </article>
      </div>
    </template>

    <form v-else-if="settings || llmSettings" class="admin-settings" @submit.prevent="saveSettings">
      <section class="admin-settings-section">
        <h2>大语言模型</h2>
        <label>服务地址<input v-model="llmConfig.llm_base_url" placeholder="https://api.example.com/v1" /></label>
        <label>模型名称<input v-model="llmConfig.llm_model" /></label>
        <label>API 密钥 <small>{{ settings?.llm_api_key_configured || llmSettings?.llm_api_key_configured ? "已配置" : "未配置" }}</small><input v-model="llmConfig.llm_api_key" type="password" placeholder="留空保持不变" /></label>
        <label>温度<input v-model.number="llmConfig.llm_temperature" type="number" min="0" max="2" step="0.1" /></label>
      </section>
      <section v-if="isSuperAdmin" class="admin-settings-section">
        <h2>Embedding 与 Rerank</h2>
        <label>Embedding 地址<input v-model="config.embedding_base_url" /></label>
        <label>Embedding 模型<input v-model="config.embedding_model" /></label>
        <label>Embedding 密钥 <small>{{ settings.embedding_api_key_configured ? "已配置" : "未配置" }}</small><input v-model="config.embedding_api_key" type="password" placeholder="留空保持不变" /></label>
        <label>Rerank 地址<input v-model="config.rerank_base_url" /></label>
        <label>Rerank 模型<input v-model="config.rerank_model" /></label>
        <label>Rerank 密钥 <small>{{ settings.rerank_api_key_configured ? "已配置" : "未配置" }}</small><input v-model="config.rerank_api_key" type="password" placeholder="留空保持不变" /></label>
      </section>
      <section v-if="isSuperAdmin" class="admin-settings-section">
        <h2>向量与存储</h2>
        <label>向量库<select v-model="config.vector_provider"><option value="local">本地检索</option><option value="milvus">Milvus</option></select></label>
        <label>Milvus 地址<input v-model="config.milvus_uri" /></label>
        <label>Collection<input v-model="config.milvus_collection" /></label>
        <label>Milvus Token <small>{{ settings.milvus_token_configured ? "已配置" : "未配置" }}</small><input v-model="config.milvus_token" type="password" placeholder="留空保持不变" /></label>
        <label>对象存储<select v-model="config.storage_provider"><option value="local">本地文件</option><option value="minio">MinIO</option></select></label>
        <label>MinIO 地址<input v-model="config.minio_endpoint" /></label>
        <label>Bucket<input v-model="config.minio_bucket" /></label>
        <label>Access Key <small>{{ settings.minio_access_key_configured ? "已配置" : "未配置" }}</small><input v-model="config.minio_access_key" type="password" placeholder="留空保持不变" /></label>
        <label>Secret Key <small>{{ settings.minio_secret_key_configured ? "已配置" : "未配置" }}</small><input v-model="config.minio_secret_key" type="password" placeholder="留空保持不变" /></label>
      </section>
      <section v-if="isSuperAdmin" class="admin-settings-section">
        <h2>处理限制</h2>
        <label>默认分块长度<input v-model.number="config.chunk_size" type="number" min="100" max="5000" /></label>
        <label>默认重叠长度<input v-model.number="config.chunk_overlap" type="number" min="0" max="2000" /></label>
        <label>最大上传 MB<input v-model.number="config.max_upload_mb" type="number" min="1" max="2048" /></label>
      </section>
      <div class="admin-save"><button class="primary-button" type="submit" :disabled="busy"><Save :size="16" />保存配置</button></div>
    </form>
  </section>
</template>
