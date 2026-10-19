import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Container, Box,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import UploadIcon from '@mui/icons-material/Upload';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <>
      <AppBar position="static" color="primary">
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700 }}>
            EyeVen
          </Typography>
          <Button
            color="inherit"
            startIcon={<DashboardIcon />}
            onClick={() => navigate('/')}
            sx={{ mr: 1, textTransform: 'none' }}
            variant={location.pathname === '/' ? 'outlined' : 'text'}
          >
            Dashboard
          </Button>
          <Button
            color="inherit"
            startIcon={<FolderOpenIcon />}
            onClick={() => navigate('/cases')}
            sx={{ mr: 1, textTransform: 'none' }}
            variant={location.pathname.startsWith('/cases') ? 'outlined' : 'text'}
          >
            Case History
          </Button>
          <Button
            color="inherit"
            startIcon={<UploadIcon />}
            onClick={() => navigate('/upload')}
            sx={{ textTransform: 'none' }}
            variant={location.pathname === '/upload' ? 'outlined' : 'text'}
          >
            Upload
          </Button>
        </Toolbar>
      </AppBar>
      <Container maxWidth="lg" sx={{ mt: 4, mb: 4 }}>
        <Box>
          <Outlet />
        </Box>
      </Container>
    </>
  );
};

export default AppLayout;
