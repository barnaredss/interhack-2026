const fs = require('fs');
const path = require('path');

module.exports = (req, res) => {
    // Resolve the full path to coords.csv dynamically
    const csvPath = path.join(__dirname, 'coords.csv');
    
    try {
        const csvData = fs.readFileSync(csvPath, 'utf8');
        res.setHeader('Content-Type', 'text/csv');
        res.status(200).send(csvData);
    } catch (error) {
        console.error(error);
        res.status(500).send('Error reading coordinates file');
    }
};