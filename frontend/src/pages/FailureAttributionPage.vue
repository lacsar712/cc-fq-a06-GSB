<template>
  <q-page class="page-pad">
    <div class="row items-center q-mb-md">
      <div class="text-h5">失败归因台</div>
      <q-space />
      <q-btn flat icon="refresh" label="刷新" @click="load" :loading="loading" />
      <q-btn
        color="primary"
        class="q-ml-sm"
        icon="download"
        label="下载摘录"
        :loading="downloading"
        @click="download"
      />
    </div>

    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row q-col-gutter-md items-end">
        <q-input
          v-model="filters.startDate"
          type="date"
          outlined
          dense
          label="起始日期"
          class="col-12 col-sm-3"
        />
        <q-input
          v-model="filters.endDate"
          type="date"
          outlined
          dense
          label="截止日期"
          class="col-12 col-sm-3"
        />
        <q-select
          v-model="filters.broken"
          :options="brokenOptions"
          outlined
          dense
          emit-value
          map-options
          label="样例是否损坏"
          class="col-12 col-sm-3"
        />
        <div class="col-12 col-sm-3 row q-gutter-sm">
          <q-btn color="primary" label="查询" @click="load" :loading="loading" />
          <q-btn flat label="重置" @click="reset" />
        </div>
      </q-card-section>
    </q-card>

    <div class="row items-center q-mb-sm q-gutter-sm">
      <q-chip color="negative" text-color="white" icon="error_outline">
        失败阶段总数：{{ result.total_failures }}
      </q-chip>
      <q-chip v-for="a in result.actors" :key="a.actor_name" outline color="primary">
        {{ a.actor_name }}：{{ a.failure_count }}
      </q-chip>
    </div>

    <div v-if="!loading && result.actors.length === 0" class="text-grey-6 q-pa-lg text-center">
      当前筛选条件下没有失败记录（skipped 阶段不计入归因）
    </div>

    <q-expansion-item
      v-for="(a, i) in result.actors"
      :key="a.actor_name"
      :default-opened="i === 0"
      expand-separator
      class="q-mb-sm bg-white rounded-borders bordered-expansion"
    >
      <template #header>
        <q-item-section>
          <div class="text-subtitle1">
            {{ a.actor_name }}
            <q-badge color="negative" class="q-ml-sm">{{ a.failure_count }} 次失败</q-badge>
            <q-badge color="grey-5" text-color="dark" class="q-ml-xs">
              {{ a.clusters.length }} 个消息簇
            </q-badge>
          </div>
        </q-item-section>
      </template>

      <q-markup-table flat dense>
        <thead>
          <tr>
            <th class="text-left">消息前缀簇</th>
            <th class="text-left">簇大小</th>
            <th class="text-left">最近作业（点作业进详情）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in a.clusters" :key="c.prefix">
            <td class="prefix-cell">{{ c.prefix }}</td>
            <td>
              <q-badge color="deep-orange" text-color="white">{{ c.size }}</q-badge>
            </td>
            <td>
              <div class="column q-gutter-xs q-py-xs">
                <div v-for="j in c.recent_jobs" :key="j.job_id" class="row items-center q-gutter-xs">
                  <q-btn
                    dense
                    flat
                    color="primary"
                    :label="`#${j.job_id}`"
                    :to="`/jobs/${j.job_id}`"
                  />
                  <span>{{ j.sample_name }}</span>
                  <q-badge :color="j.is_broken ? 'negative' : 'positive'">
                    {{ j.is_broken ? '损坏' : '合格' }}
                  </q-badge>
                  <span class="text-grey-6 text-caption">{{ formatTime(j.created_at) }}</span>
                </div>
              </div>
            </td>
          </tr>
        </tbody>
      </q-markup-table>
    </q-expansion-item>
  </q-page>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { downloadFailureExcerpt, getFailureAttribution } from '../api/client'

const $q = useQuasar()
const loading = ref(false)
const downloading = ref(false)
const result = ref({ total_failures: 0, actors: [] })

const filters = reactive({
  startDate: '',
  endDate: '',
  broken: 'all',
})

const brokenOptions = [
  { label: '全部样例', value: 'all' },
  { label: '仅损坏样例', value: 'true' },
  { label: '仅合格样例', value: 'false' },
]

function buildParams() {
  const params = {}
  if (filters.startDate) {
    const d = new Date(`${filters.startDate}T00:00:00`)
    params.start = d.toISOString()
  }
  if (filters.endDate) {
    const d = new Date(`${filters.endDate}T23:59:59.999`)
    params.end = d.toISOString()
  }
  if (filters.broken !== 'all') {
    params.broken = filters.broken
  }
  return params
}

function formatTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

async function load() {
  loading.value = true
  try {
    result.value = await getFailureAttribution(buildParams())
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '加载失败归因失败' })
  } finally {
    loading.value = false
  }
}

function reset() {
  filters.startDate = ''
  filters.endDate = ''
  filters.broken = 'all'
  load()
}

async function download() {
  downloading.value = true
  try {
    await downloadFailureExcerpt(buildParams())
    $q.notify({ type: 'positive', message: '摘录已开始下载' })
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '下载摘录失败' })
  } finally {
    downloading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.bordered-expansion {
  border: 1px solid rgba(0, 0, 0, 0.12);
}
.prefix-cell {
  font-family: monospace;
  white-space: normal;
  word-break: break-all;
}
</style>
