<script setup lang="ts">
import { ref } from 'vue'
import Board from './Board.vue'
import Legacy from './App.vue'
const legacy = ref(false)
</script>
<template>
  <div class="mode-bar"><strong>续航</strong><button @click="legacy = !legacy">{{ legacy ? '返回团队工作台' : '打开原工作空间' }}</button></div>
  <Legacy v-if="legacy" /><Board v-else />
</template>
<style scoped>
.mode-bar { display:flex; justify-content:space-between; align-items:center; padding:10px 24px; background:#142c28; color:white; }
.mode-bar button { background:transparent; border:1px solid #76968b; border-radius:6px; color:white; padding:6px 12px; cursor:pointer; }
</style>
