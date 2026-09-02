const { Document, Packer, Paragraph, TextRun, HeadingLevel } = require('docx');

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

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    const study = req.body || {};
    const pitchAngles = Array.isArray(study.pitch_angles) ? study.pitch_angles : [];

    const children = [heading(study.headline || 'Untitled study', HeadingLevel.HEADING_1)];

    children.push(labeled('Journal', study.journal));
    children.push(labeled('Publication date', study.pubdate));
    children.push(labeled('Category', study.category));
    children.push(labeled('Source', study.source_label));
    children.push(labeled('Groundbreaking', study.groundbreaking));
    children.push(labeled('PMID', study.pmid));

    if (study.summary) {
      children.push(heading('Summary', HeadingLevel.HEADING_2));
      children.push(new Paragraph({ text: study.summary, spacing: { after: 120 } }));
    }

    if (study.why_it_matters) {
      children.push(heading('Why It Matters', HeadingLevel.HEADING_2));
      children.push(new Paragraph({ text: study.why_it_matters, spacing: { after: 120 } }));
    }

    if (study.caveats) {
      children.push(heading('Caveats', HeadingLevel.HEADING_2));
      children.push(new Paragraph({ text: study.caveats, spacing: { after: 120 } }));
    }

    if (study.fact_check_note) {
      children.push(heading('Fact-Check Note', HeadingLevel.HEADING_2));
      children.push(new Paragraph({ text: study.fact_check_note, spacing: { after: 120 } }));
    }

    if (study.media_coverage) {
      children.push(heading('Media Coverage', HeadingLevel.HEADING_2));
      children.push(new Paragraph({ text: study.media_coverage, spacing: { after: 120 } }));
    }

    if (pitchAngles.length) {
      children.push(heading('Pitch Angles', HeadingLevel.HEADING_2));
      for (const pitch of pitchAngles) {
        children.push(heading(pitch.publication_type || 'Pitch', HeadingLevel.HEADING_3));
        const field = labeled('Headline', pitch.headline);
        if (field) children.push(field);
        const hook = labeled('Opening hook', pitch.hook);
        if (hook) children.push(hook);
        const angle = labeled('Pitch angle', pitch.pitch_angle);
        if (angle) children.push(angle);
        const fits = labeled('Why it fits', pitch.why_it_fits);
        if (fits) children.push(fits);
        const caveats = labeled('Caveats to flag', pitch.caveats_to_flag);
        if (caveats) children.push(caveats);
      }
    }

    const doc = new Document({ sections: [{ children: children.filter(Boolean) }] });
    const buffer = await Packer.toBuffer(doc);

    const filename = sanitizeFilename(study.headline) + '.docx';
    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.status(200).send(buffer);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: String(err) });
  }
};
