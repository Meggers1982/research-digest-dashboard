const { Document, Packer, Paragraph, TextRun, HeadingLevel } = require('docx');

const LEVELS = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3, HeadingLevel.HEADING_4];

function heading(text, level) {
  return new Paragraph({ text, heading: level, spacing: { before: 240, after: 120 } });
}

function labeled(label, value) {
  if (!value) return null;
  return new Paragraph({
    children: [new TextRun({ text: label + ': ', bold: true }), new TextRun(String(value))],
    spacing: { after: 120 },
  });
}

function sanitizeFilename(value) {
  return String(value || 'study')
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'study';
}

// One study's paragraphs. `depth` 0 puts the headline at H1 (single-study
// export); 1 puts it at H2 under a digest heading (digest download).
function studyBlocks(study, depth) {
  const h = i => LEVELS[Math.min(depth + i, LEVELS.length - 1)];
  const pitchAngles = Array.isArray(study.pitch_angles) ? study.pitch_angles : [];

  const children = [heading(study.headline || 'Untitled study', h(0))];

  children.push(labeled('Journal', study.journal));
  children.push(labeled('Publication date', study.pubdate));
  children.push(labeled('Category', study.category));
  children.push(labeled('Source', study.source_label));
  children.push(labeled('Groundbreaking', study.groundbreaking));
  children.push(labeled('Relevance score', study.relevance_score));
  children.push(labeled('PMID', study.pmid));
  children.push(labeled('DOI', study.doi));

  for (const [title, value] of [
    ['Summary', study.summary],
    ['Why It Matters', study.why_it_matters],
    ['Caveats', study.caveats],
    ['Fact-Check Note', study.fact_check_note],
    ['Media Coverage', study.media_coverage],
  ]) {
    if (!value) continue;
    children.push(heading(title, h(1)));
    children.push(new Paragraph({ text: String(value), spacing: { after: 120 } }));
  }

  if (pitchAngles.length) {
    children.push(heading('Pitch Angles', h(1)));
    for (const pitch of pitchAngles) {
      children.push(heading(pitch.publication_type || 'Pitch', h(2)));
      children.push(labeled('Headline', pitch.headline));
      children.push(labeled('Opening hook', pitch.hook));
      children.push(labeled('Pitch angle', pitch.pitch_angle));
      children.push(labeled('Why it fits', pitch.why_it_fits));
      children.push(labeled('Caveats to flag', pitch.caveats_to_flag));
    }
  }

  return children;
}

// Body is either one study (per-card export) or
// { title, digests: [{ label, studies: [...] }] } (download by digest).
function buildChildren(body) {
  if (!Array.isArray(body.digests)) return { children: studyBlocks(body, 0), name: body.headline };

  const children = [heading(body.title || 'Research digest studies', HeadingLevel.TITLE)];
  if (body.subtitle) children.push(new Paragraph({ text: body.subtitle, spacing: { after: 240 } }));
  for (const digest of body.digests) {
    const studies = Array.isArray(digest.studies) ? digest.studies : [];
    children.push(heading(`${digest.label || 'Digest'} (${studies.length})`, HeadingLevel.HEADING_1));
    for (const study of studies) children.push(...studyBlocks(study, 1));
  }
  return { children, name: body.title };
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    const { children, name } = buildChildren(req.body || {});
    const doc = new Document({ sections: [{ children: children.filter(Boolean) }] });
    const buffer = await Packer.toBuffer(doc);

    const filename = sanitizeFilename(name) + '.docx';
    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.status(200).send(buffer);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: String(err) });
  }
};

module.exports.buildChildren = buildChildren;
