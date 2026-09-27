// NP-06 首次运行向导：触发判定与常量。
// 判定原则：用户看过向导（flag 在）永不再弹；没看过且后端报告尚未配置
// LLM Key（GET /api/config 的 llm_api_key_masked 为空串）才弹——老用户升级不被打扰。
export const FIRST_RUN_FLAG = 'tuzhan.firstrun.v1'

export function shouldShowFirstRun(flag: string | null, configPayload: unknown): boolean {
  if (flag) return false
  const config = (configPayload as { config?: { llm_api_key_masked?: string } } | null | undefined)
    ?.config
  // 响应形态不认识 → 保守不弹（后端未就绪/异常时打扰用户是更糟的体验）
  if (!config || typeof config !== 'object') return false
  return !config.llm_api_key_masked
}
