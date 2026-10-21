export type Src = { file: string; line: number; id: string }
export type Row = {
  time: string | null
  title: string
  kind?: string
  label?: string
  focus?: boolean
  private?: boolean
  src?: Src
}
export type Day = { date: string; rows: Row[] }
export type Brief = {
  date: string
  at: string
  calendar: string
  // is_today が false = 今日はもう終わっていて、three は明日の分。
  lead?: { is_today?: boolean }
  three: Row[]
  plan: Day[]
}
// 開いている backlog 項目1つ。bin/menu --json --domains から。
export type Item = {
  title: string
  state: string
  star: boolean
  decision: boolean
  due: string | null
  late: boolean
  private: boolean
  src: Src
}
// ドメインで最後に起きたこと。ago は bin/menu の「今日 / 3日前 / 2週間前 …」。
export type Last = { ago: string; day: string | null; note: string; private: boolean }
export type Domain = {
  n: number | null
  slug: string
  name: string
  parent: string | null
  lane: string
  last?: Last | null
  items: Item[]
}
export type Domains = { at: string; domains: Domain[] }
export type Status = { at: string; error: string | null }
export type SendState = 'pending' | 'accepted' | 'dropped' | 'moved' | 'refused'
export type Send = { id: string; state: SendState; note: string; title: string; private: boolean } | null

declare module 'claude-code' {
  interface PluginState {
    'ai-panel': {
      brief: Brief | null
      status: Status
      send: Send
      menu: boolean
      doms: Domains | null
      domStatus: Status
      openDom: string | null
      showEmpty: boolean
      openItem: string | null
    }
  }
}
