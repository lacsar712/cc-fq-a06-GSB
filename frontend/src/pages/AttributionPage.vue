<template>
  <q-page class="page-pad">
    <div class="row items-center q-mb-md">
      <div class="text-h5">失败归因与话术聚类台</div>
      <q-space />
      <q-btn flat icon="refresh" label="刷新" @click="load" :loading="loading" />
    </div>

    <!-- 服务端筛选 -->
    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row q-col-gutter-md items-end q-pb-none">
        <div class="col-12 col-sm-6 col-md-3">
          <div class="text-caption text-grey-7 q-mb-xs">起始日期（含当天）</div>
          <q-input
            v-model="filters.startDate"
            type="date"
            outlined
            dense
            :rules="[(v) => !v || /^\d{4}-\d{2}-\d{2}$/.test(v) || '日期格式为 YYYY-MM-DD']"
          />
        </div>
        <div class="col-12 col-sm-6 col-md-3">
          <div class="text-caption text-grey-7 q-mb-xs">结束日期（含当天）</div>
          <q-input
            v-model="filters.endDate"
            type="date"
            outlined
            dense
            :rules="[(v) => !v || /^\d{4}-\d{2}-\d{2}$/.test(v) || '日期格式为 YYYY-MM-DD']"
          />
        </div>
        <div class="col-12 col-sm-6 col-md-3">
          <div class="text-caption text-grey-7 q-mb-xs">样例类型</div>
          <q-btn-toggle
            v-model="filters.broken"
            spread
            no-caps
            push
            glossy
            toggle-color="primary"
            :options="[
              { label: '全部', value: '' },
              { label: '损坏样例', value: 'true' },
              { label: '合格样例', value: 'false' },
            ]"
          />
        </div>
        <div class="col-12 col-sm-6 col-md-3">
          <div class="row q-gutter-sm">
            <q-btn color="primary" icon="search" label="查询" @click="load" :loading="loading" />
            <q-btn flat icon="restart_alt" label="重置" @click="resetFilters" />
            <q-btn
              outline
              color="secondary"
              icon="download"
              label="下载摘录"
              :loading="downloading"
              @click="downloadCsv"
            />
          </div>
        </div>
      </q-card-section>
      <q-card-section class="text-caption text-grey-6">
        仅统计状态为 failed 的阶段；因上游失败而 skipped 的阶段不计入归因。自定义输入不参与“损坏/合格”分档。
      </q-card-section>
    </q-card>

    <div class="row items-center q-mb-sm">
      <div class="text-subtitle1">
        失败 Actor 计数
        <q-badge color="primary" class="q-ml-sm">共 {{ data.total_failures ?? 0 }} 次失败</q-badge>
      </div>
    </div>

    <q-banner v-if="!loading && actors.length === 0" rounded class="bg-grey-2 q-mb-md">
      当前筛选条件下没有失败归因记录。
    </q-banner>

    <q-card v-for="actor in actors" :key="actor.actor_name" flat bordered class="q-mb-md">
      <q-expansion-item
        :model-value="openedActors[actor.actor_name]"
        @update:model-value="(v) => toggleActor(actor.actor_name, v)"
        :header-style="{ width: '100%' }"
      >
        <template #header>
          <div class="row items-center full-width">
            <q-icon name="memory" class="q-mr-sm text-primary" />
            <div class="text-subtitle2">{{ actor.actor_name }}</div>
            <q-badge color="negative" class="q-ml-md">{{ actor.failure_count }} 次</q-badge>
            <q-space />
            <div class="text-caption text-grey-7 q-mr-md">
              最近失败：{{ fmt(actor.latest_failure_at) }}
            </div>
            <q-badge v-if="actor.clusters.length" color="amber" text-color="dark">
              {{ actor.clusters.length }} 个话术簇
            </q-badge>
          </div>
        </template>

        <q-separator />
        <div class="q-pa-md">
          <div class="text-caption text-grey-7 q-mb-sm">按失败消息前缀聚类（数字/输入值已归一化）</div>
          <q-card v-for="c in actor.clusters" :key="c.cluster_prefix" flat bordered class="q-mb-sm bg-grey-1">
            <q-expansion-item
              :model-value="openedClusters[clusterKey(actor.actor_name, c.cluster_prefix)]"
              @update:model-value="(v) => toggleCluster(actor.actor_name, c.cluster_prefix, v)"
            >
              <template #header>
                <div class="row items-center full-width">
                  <q-badge color="deep-orange" class="q-mr-sm">{{ c.cluster_size }}</q-badge>
                  <div class="text-body2 cluster-text">{{ c.cluster_prefix || '（空消息）' }}</div>
                  <q-space />
                  <div class="text-caption text-grey-7 q-mr-sm">
                    最近作业
                    <q-btn
                      flat
                      dense
                      no-caps
                      color="primary"
                      :label="`#${c.latest_job_id}`"
                      :to="`/jobs/${c.latest_job_id}`"
                      @click.stop
                    />
                    {{ fmt(c.latest_job_created_at) }}
                  </div>
                </div>
              </template>
              <q-separator />
              <q-list dense>
                <q-item v-for="j in c.recent_jobs" :key="j.job_id">
                  <q-item-section>
                    <q-item-label caption>
                      <q-btn
                        flat
                        dense
                        no-caps
                        color="primary"
                        :label="`作业 #${j.job_id}`"
                        :to="`/jobs/${j.job_id}`"
                        class="q-px-none"
                        @click.stop
                      />
                      · {{ j.sample_name }}
                      · {{ brokenLabel(j.is_broken) }}
                      · 提交人 {{ j.created_by }}
                      · {{ fmt(j.created_at) }}
                    </q-item-label>
                    <q-item-label class="cluster-text">{{ j.message }}</q-item-label>
                  </q-item-section>
                </q-item>
                <q-item v-if="c.cluster_size > c.recent_jobs.length">
                  <q-item-section>
                    <q-btn
                      flat
                      dense
                      color="primary"
                      icon="unfold_more"
                      :label="`查看该簇全部 ${c.cluster_size} 个作业`"
                      :loading="loadingJobs[clusterKey(actor.actor_name, c.cluster_prefix)]"
                      @click.stop="showAllJobs(actor.actor_name, c)"
                    />
                  </q-item-section>
                </q-item>
              </q-list>
            </q-expansion-item>
          </q-card>
        </div>
      </q-expansion-item>
    </q-card>

    <!-- 全簇作业对话框 -->
    <q-dialog v-model="jobDialog.show">
      <q-card style="min-width: 640px; max-width: 90vw">
        <q-card-section class="row items-center">
          <div class="text-subtitle2">
            {{ jobDialog.actor }} · 簇内全部作业（{{ jobDialog.jobs.length }}）
          </div>
          <q-space />
          <q-btn flat icon="close" v-close-popup />
        </q-card-section>
        <q-card-actions class="q-pa-none">
          <q-list dense bordered separator class="full-width">
            <q-item v-for="j in jobDialog.jobs" :key="j.job_id">
              <q-item-section>
                <q-item-label>
                  <q-btn
                    flat
                    dense
                    color="primary"
                    :label="`作业 #${j.job_id}`"
                    :to="`/jobs/${j.job_id}`"
                    @click="jobDialog.show = false"
                  />
                  · {{ j.sample_name }} · {{ brokenLabel(j.is_broken) }} · {{ fmt(j.created_at) }}
                </q-item-label>
                <q-item-label caption class="cluster-text">{{ j.message }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { downloadFailureExport, getClusterJobs, getFailureAttribution } from '../api/client'

const $q = useQuasar()
const loading = ref(false)
const downloading = ref(false)
const data = ref({ actors: [], total_failures: 0 })
const actors = ref([])

const filters = reactive({ startDate: '', endDate: '', broken: '' })
const openedActors = reactive({})
const openedClusters = reactive({})
const loadingJobs = reactive({})

const jobDialog = reactive({ show: false, actor: '', jobs: [] })

function clusterKey(actor, prefix) {
  return `${actor} ${prefix}`
}

function toggleActor(name, v) {
  openedActors[name] = v
}

function toggleCluster(actor, prefix, v) {
  openedClusters[clusterKey(actor, prefix)] = v
}

function queryParams() {
  const params = {}
  if (filters.startDate) params.start_date = filters.startDate
  if (filters.endDate) params.end_date = filters.endDate
  if (filters.broken !== '') params.is_broken = filters.broken
  return params
}

async function load() {
  loading.value = true
  try {
    data.value = await getFailureAttribution(queryParams())
    actors.value = data.value.actors || []
    // 展开状态随新结果保留；无数据时清空
    if (actors.value.length === 0) {
      Object.keys(openedActors).forEach((k) => delete openedActors[k])
    }
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '加载失败' })
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.startDate = ''
  filters.endDate = ''
  filters.broken = ''
  load()
}

async function downloadCsv() {
  downloading.value = true
  try {
    const { total } = await downloadFailureExport(queryParams())
    $q.notify({
      type: 'positive',
      message: `摘录已下载：${total ?? 0} 条失败阶段记录（UTF-8 CSV）`,
    })
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '下载失败' })
  } finally {
    downloading.value = false
  }
}

async function showAllJobs(actor, cluster) {
  const key = clusterKey(actor, cluster.cluster_prefix)
  loadingJobs[key] = true
  try {
    jobDialog.actor = actor
    jobDialog.jobs = await getClusterJobs(actor, {
      ...queryParams(),
      cluster_prefix: cluster.cluster_prefix,
    })
    jobDialog.show = true
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '加载失败' })
  } finally {
    loadingJobs[key] = false
  }
}

function fmt(v) {
  return v ? new Date(v).toLocaleString() : '—'
}

function brokenLabel(v) {
  if (v === true) return '损坏'
  if (v === false) return '合格'
  return '自定义输入'
}

onMounted(load)
</script>

<style scoped>
.cluster-text {
  word-break: break-all;
  white-space: pre-wrap;
}
</style>
