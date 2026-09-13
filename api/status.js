const { neon } = require('@neondatabase/serverless');

const sql = neon(process.env.DATABASE_URL);

module.exports = async function handler(req, res) {
  try {
    if (req.method === 'GET') {
      const rows = await sql`select study_id, status from study_status`;
      res.status(200).json(rows);
      return;
    }

    if (req.method === 'POST') {
      const body = req.body || {};
      const items = Array.isArray(body) ? body : [body];
      for (const item of items) {
        const { study_id, status, updated_at } = item;
        if (!study_id || !status) continue;
        await sql`
          insert into study_status (study_id, status, updated_at)
          values (${study_id}, ${status}, ${updated_at || new Date().toISOString()})
          on conflict (study_id) do update set status = excluded.status, updated_at = excluded.updated_at
        `;
      }
      res.status(200).json({ ok: true });
      return;
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    // The detail stays in the Vercel logs. This endpoint is public, and a Neon
    // driver error can quote the whole DATABASE_URL, password included -- that
    // is how Pawket's /api/health published one on 2026-09-08.
    console.error(err);
    res.status(500).json({ error: 'Status store unavailable' });
  }
};
