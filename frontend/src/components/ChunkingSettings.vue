<script setup lang="ts">
import { Braces, FileStack, Layers3, RotateCcw, SlidersHorizontal, X } from "lucide-vue-next";
import type { ChunkingConfig } from "../api";

const props = defineProps<{
  config: ChunkingConfig;
  documentCount: number;
  busy: boolean;
}>();

const emit = defineEmits<{
  close: [];
  "update:config": [value: ChunkingConfig];
  reindex: [];
}>();

function update<K extends keyof ChunkingConfig>(key: K, value: ChunkingConfig[K]) {
  emit("update:config", { ...props.config, [key]: value });
}

function updateNumber(key: "chunk_size" | "chunk_overlap", event: Event) {
  const value = Number((event.target as HTMLInputElement).value);
  update(key, Number.isFinite(value) ? value : 0);
}
</script>

<template>
  <div class="settings-backdrop" @click.self="emit('close')">
    <section class="settings-panel" aria-label="文档分块设置">
      <header class="settings-header">
        <div>
          <p>文档处理</p>
          <h2>分块设置</h2>
        </div>
        <button class="settings-icon-button" type="button" title="关闭设置" @click="emit('close')"><X :size="19" /></button>
      </header>

      <div class="settings-content">
        <section class="settings-section">
          <div class="section-heading">
            <div class="section-icon"><SlidersHorizontal :size="17" /></div>
            <div><h3>分块模型</h3><p>选择文档分块时优先识别的边界。</p></div>
          </div>

          <div class="model-list">
            <label class="model-option" :class="{ selected: config.chunk_model === 'interface' }">
              <input type="radio" name="chunk-model" :checked="config.chunk_model === 'interface'" @change="update('chunk_model', 'interface')" />
              <Braces :size="18" />
              <span><b>接口语义</b><small>按接口标题、方法、路径、参数、示例和响应进行聚合。</small></span>
            </label>
            <label class="model-option" :class="{ selected: config.chunk_model === 'structured' }">
              <input type="radio" name="chunk-model" :checked="config.chunk_model === 'structured'" @change="update('chunk_model', 'structured')" />
              <FileStack :size="18" />
              <span><b>标题结构</b><small>优先按 Markdown 标题、章节和编号标题分块。</small></span>
            </label>
            <label class="model-option" :class="{ selected: config.chunk_model === 'fixed' }">
              <input type="radio" name="chunk-model" :checked="config.chunk_model === 'fixed'" @change="update('chunk_model', 'fixed')" />
              <Layers3 :size="18" />
              <span><b>固定窗口</b><small>不依赖文档结构，按固定字符长度分块。</small></span>
            </label>
            <label class="model-option" :class="{ selected: config.chunk_model === 'delimiter' }">
              <input type="radio" name="chunk-model" :checked="config.chunk_model === 'delimiter'" @change="update('chunk_model', 'delimiter')" />
              <FileStack :size="18" />
              <span><b>自定义分隔符</b><small>每次遇到指定分隔符时开始新的分块。</small></span>
            </label>
            <label v-if="config.chunk_model === 'delimiter'" class="delimiter-field">
              <span>分隔符</span>
              <input
                :value="config.custom_delimiter"
                placeholder="例如：========================"
                @input="update('custom_delimiter', ($event.target as HTMLInputElement).value)"
              />
            </label>
          </div>
        </section>

        <section class="settings-section">
          <div class="section-heading compact">
            <div class="section-icon"><Layers3 :size="17" /></div>
            <div><h3>分块规则</h3><p>长度按字符计算；重叠内容有助于保留分块之间的上下文。</p></div>
          </div>

          <div class="number-grid">
            <label class="number-field">
              <span>单块最大长度</span>
              <div><input :value="config.chunk_size" min="100" max="5000" step="50" type="number" @input="updateNumber('chunk_size', $event)" /><small>字符</small></div>
            </label>
            <label class="number-field">
              <span>分块重叠长度</span>
              <div><input :value="config.chunk_overlap" min="0" :max="Math.max(0, config.chunk_size - 1)" step="10" type="number" @input="updateNumber('chunk_overlap', $event)" /><small>字符</small></div>
            </label>
          </div>

          <label class="switch-row">
            <span><b>保留标题和接口上下文</b><small>将标题或接口上下文加入每个分块，提升检索效果。</small></span>
            <input type="checkbox" :checked="config.retain_context" @change="update('retain_context', ($event.target as HTMLInputElement).checked)" />
          </label>
          <label class="switch-row">
            <span><b>按 PDF 页分割</b><small>避免分块跨页，并保留页码信息用于引用。</small></span>
            <input type="checkbox" :checked="config.split_by_page" @change="update('split_by_page', ($event.target as HTMLInputElement).checked)" />
          </label>
        </section>

        <p v-if="config.chunk_overlap >= config.chunk_size" class="settings-error">分块重叠长度必须小于单块最大长度。</p>
        <p v-else-if="config.chunk_model === 'delimiter' && !config.custom_delimiter" class="settings-error">导入或重新分块前请填写分隔符。</p>
        <p v-else class="settings-note">这些设置会应用于下一次文档导入；重新分块后会应用到已有文档。</p>
      </div>

      <footer class="settings-actions">
        <button
          v-if="documentCount"
          class="reindex-button"
          type="button"
          :disabled="busy || config.chunk_overlap >= config.chunk_size || (config.chunk_model === 'delimiter' && !config.custom_delimiter)"
          @click="emit('reindex')"
        >
          <RotateCcw :size="16" />
          {{ busy ? "正在重新分块" : `重新分块 ${documentCount} 个文档` }}
        </button>
        <button class="save-button" type="button" :disabled="config.chunk_overlap >= config.chunk_size || (config.chunk_model === 'delimiter' && !config.custom_delimiter)" @click="emit('close')">保存设置</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.settings-backdrop { position: fixed; inset: 0; z-index: 10; display: grid; place-items: center; padding: 24px; background: rgba(22, 37, 43, .38); }
.settings-panel { display: flex; flex-direction: column; width: min(620px, 100%); max-height: calc(100vh - 48px); overflow: hidden; color: #263741; background: #fff; border-radius: 8px; box-shadow: 0 24px 60px rgba(19, 37, 43, .25); }
.settings-header { display: flex; align-items: center; justify-content: space-between; padding: 19px 22px 17px; border-bottom: 1px solid #e5ecee; }
.settings-header p { margin: 0 0 4px; color: #428475; font-size: 11px; font-weight: 700; }.settings-header h2 { margin: 0; font-size: 19px; }.settings-icon-button { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; color: #61717a; background: transparent; border: 0; border-radius: 5px; }.settings-icon-button:hover { background: #edf3f3; }
.settings-content { padding: 4px 22px 22px; overflow-y: auto; }.settings-section { padding: 19px 0; border-bottom: 1px solid #e7edef; }.section-heading { display: flex; gap: 9px; align-items: flex-start; margin-bottom: 13px; }.section-heading.compact { margin-bottom: 15px; }.section-icon { display: grid; flex: none; place-items: center; width: 31px; height: 31px; color: #2c756b; background: #e5f2ef; border-radius: 5px; }.section-heading h3 { margin: 1px 0 3px; font-size: 14px; }.section-heading p { margin: 0; color: #74838b; font-size: 12px; line-height: 1.45; }
.model-list { display: grid; gap: 7px; }.model-option { display: grid; grid-template-columns: 18px 20px 1fr; gap: 10px; align-items: start; padding: 10px 11px; color: #60717a; border: 1px solid #dbe5e6; border-radius: 6px; cursor: pointer; }.model-option.selected { color: #236f66; background: #f1f8f6; border-color: #69a99e; }.model-option input { margin: 2px 0 0; accent-color: #1e786d; }.model-option svg { margin-top: 1px; }.model-option b, .model-option small { display: block; }.model-option b { color: #2d3d46; font-size: 13px; }.model-option small { margin-top: 2px; color: #76858d; font-size: 11px; line-height: 1.45; }
.delimiter-field { display: grid; gap: 6px; padding: 3px 0 0 49px; color: #52636c; font-size: 12px; font-weight: 650; }.delimiter-field input { width: 100%; height: 35px; padding: 0 9px; color: #263741; outline: 0; border: 1px solid #d9e3e5; border-radius: 5px; font-size: 13px; font-weight: 400; }.delimiter-field input:focus { border-color: #59a79d; box-shadow: 0 0 0 2px #d9ece8; }
.number-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.number-field { display: grid; gap: 6px; color: #52636c; font-size: 12px; font-weight: 650; }.number-field > div { display: flex; align-items: center; background: #fff; border: 1px solid #d9e3e5; border-radius: 5px; }.number-field input { width: 100%; height: 35px; min-width: 0; padding: 0 9px; color: #263741; outline: 0; border: 0; border-radius: 5px; }.number-field input:focus { box-shadow: 0 0 0 2px #d9ece8; }.number-field small { padding-right: 9px; color: #84929a; font-size: 11px; font-weight: 400; }
.switch-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; padding: 14px 0 0; cursor: pointer; }.switch-row b, .switch-row small { display: block; }.switch-row b { color: #3e4e57; font-size: 12px; }.switch-row small { max-width: 410px; margin-top: 3px; color: #7b8991; font-size: 11px; line-height: 1.45; }.switch-row input { flex: none; width: 34px; height: 19px; margin: 0; accent-color: #187a73; }
.settings-note, .settings-error { margin: 15px 0 0; font-size: 12px; line-height: 1.5; }.settings-note { color: #71818a; }.settings-error { color: #ad4535; }.settings-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 15px 22px; background: #fbfcfc; border-top: 1px solid #e5ecee; }.save-button, .reindex-button { display: inline-flex; justify-content: center; align-items: center; gap: 7px; min-height: 35px; padding: 0 12px; border-radius: 5px; font-size: 12px; font-weight: 650; }.save-button { color: #fff; background: #187a73; border: 1px solid #187a73; }.save-button:hover:not(:disabled) { background: #12645f; }.reindex-button { margin-right: auto; color: #276f66; background: #fff; border: 1px solid #bddbd5; }.reindex-button:hover:not(:disabled) { background: #eef8f6; }.save-button:disabled, .reindex-button:disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 560px) { .settings-backdrop { padding: 12px; }.settings-panel { max-height: calc(100vh - 24px); }.settings-content, .settings-header, .settings-actions { padding-left: 16px; padding-right: 16px; }.number-grid { grid-template-columns: 1fr; }.settings-actions { flex-wrap: wrap; }.reindex-button { width: 100%; margin-right: 0; }.save-button { margin-left: auto; } }
</style>
