import { describe, expect, it } from "vitest";

import transcriptViewSource from "./TranscriptView.svelte?raw";

describe("TranscriptView 导出按钮", () => {
  it("按逐字稿、字幕稿、会话稿顺序绑定正确格式", () => {
    const plain = transcriptViewSource.indexOf(">导出逐字稿</button>");
    const subtitle = transcriptViewSource.indexOf(">导出字幕稿</button>");
    const conversation = transcriptViewSource.indexOf(">导出会话稿</button>");

    expect(plain).toBeGreaterThan(-1);
    expect(subtitle).toBeGreaterThan(plain);
    expect(conversation).toBeGreaterThan(subtitle);
    expect(transcriptViewSource).toContain(
      'onclick={() => exportAs("plain")}>导出逐字稿</button>',
    );
    expect(transcriptViewSource).toContain(
      'onclick={() => exportAs("srt")}>导出字幕稿</button>',
    );
    expect(transcriptViewSource).toContain(
      'onclick={() => exportAs("txt")}>导出会话稿</button>',
    );
  });
});
