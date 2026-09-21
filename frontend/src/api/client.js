import axios from 'axios'
import { useAuthStore } from '../stores/auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 20000,
})

api.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') {
      err.message = detail
    } else if (Array.isArray(detail)) {
      err.message = detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
    }
    return Promise.reject(err)
  },
)

export async function login(username, password) {
  const { data } = await api.post('/auth/login', { username, password })
  return data
}

export async function getHealth() {
  const { data } = await api.get('/health')
  return data
}

export async function listSamples() {
  const { data } = await api.get('/samples')
  return data
}

export async function listJobs() {
  const { data } = await api.get('/jobs')
  return data
}

export async function getJob(id) {
  const { data } = await api.get(`/jobs/${id}`)
  return data
}

export async function getJobStages(id) {
  const { data } = await api.get(`/jobs/${id}/stages`)
  return data
}

export async function createJob(body) {
  const { data } = await api.post('/jobs', body)
  return data
}

export async function getFailureAttribution(params = {}) {
  const { data } = await api.get('/attribution/failures', { params })
  return data
}

export async function getClusterJobs(actorName, params) {
  const { data } = await api.get(`/attribution/failures/${encodeURIComponent(actorName)}/jobs`, {
    params,
  })
  return data
}

export async function downloadFailureExport(params = {}) {
  // 带鉴权头取 CSV，再交给浏览器下载（响应已含 Content-Disposition）
  const res = await api.get('/attribution/export', { params, responseType: 'blob' })
  const disposition = res.headers['content-disposition'] || ''
  let filename = 'failure_attribution.csv'
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  if (star) filename = decodeURIComponent(star[1])
  else if (plain) filename = plain[1]
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return { filename, total: res.headers['x-total-failures'] }
}

export default api
