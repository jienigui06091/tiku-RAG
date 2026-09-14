<script setup lang="ts">
import { ref } from "vue";
import { BookOpen, LoaderCircle, LogIn } from "lucide-vue-next";

defineProps<{
  ready: boolean;
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{ login: [payload: { username: string; password: string }] }>();
const username = ref("");
const password = ref("");

function submit() {
  emit("login", { username: username.value.trim(), password: password.value });
}
</script>

<template>
  <main class="login-page">
    <form class="login-panel" @submit.prevent="submit">
      <div class="login-brand"><BookOpen :size="22" /><strong>Tiku RAG</strong></div>
      <h1>登录工作台</h1>
      <p v-if="!ready" class="login-note">尚未初始化管理员。请在服务端设置 BOOTSTRAP_ADMIN_USERNAME 和 BOOTSTRAP_ADMIN_PASSWORD 后重启服务。</p>
      <template v-else>
        <label>用户名<input v-model="username" autocomplete="username" required /></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" required /></label>
        <p v-if="error" class="modal-error">{{ error }}</p>
        <button class="primary-button login-submit" type="submit" :disabled="loading">
          <LoaderCircle v-if="loading" class="spin" :size="17" />
          <LogIn v-else :size="17" />
          登录
        </button>
      </template>
    </form>
  </main>
</template>
