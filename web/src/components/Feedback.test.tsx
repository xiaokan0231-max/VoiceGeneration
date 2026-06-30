import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Dialog } from './Feedback'

function Fixture() {
  const [open, setOpen] = useState(false)
  return <><button onClick={() => setOpen(true)}>打开</button><Dialog open={open} title="测试弹窗" onClose={() => setOpen(false)}><input data-autofocus aria-label="名称" /></Dialog></>
}

describe('Dialog', () => {
  it('moves focus inside, closes with Escape, and restores focus', async () => {
    render(<Fixture />)
    const opener = screen.getByRole('button', { name: '打开' })
    opener.focus()
    fireEvent.click(opener)
    const input = screen.getByRole('textbox', { name: '名称' })
    await waitFor(() => expect(input).toHaveFocus())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(opener).toHaveFocus())
  })
})
