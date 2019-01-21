const webpack = require('webpack');

module.exports = {
  configureWebpack: (config) => {
    config.plugins.push(
      new webpack.DefinePlugin({
        GIRDER_API_ROOT: `"${process.env.GIRDER_API_ROOT}"` || '"/api/v1"'
      }),
    );
  },
};
