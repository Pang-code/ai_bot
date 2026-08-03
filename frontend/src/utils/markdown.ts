import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true,
})

export function renderMarkdown(content: string): string {
  return markdown.render(content)
}
